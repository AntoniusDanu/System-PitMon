#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "nvs_flash.h"

#include "lwip/netif.h"
#include "lwip/sio.h"
#include "lwip/ip_addr.h"
#include "netif/ppp/pppapi.h"
#include "netif/ppp/pppos.h"
#include "netif/ppp/ppp.h"

#define MODEM_UART_NUM UART_NUM_1
#define MODEM_TXD      (GPIO_NUM_17)
#define MODEM_RXD      (GPIO_NUM_18)
#define MODEM_RTS      (UART_PIN_NO_CHANGE)
#define MODEM_CTS      (UART_PIN_NO_CHANGE)
#define BUF_SIZE       (1024)

#ifndef PPPAUTHTYPE_NONE
#define PPPAUTHTYPE_NONE 0
#endif


static const char *TAG = "PPP_AP";
static struct netif ppp_netif;
static ppp_pcb *ppp = NULL;
static esp_netif_t *ap_netif = NULL;
static esp_netif_t *ppp_netif_esp = NULL;

/**
 * Fungsi callback output PPP (modem TX)
 */
 
u32_t ppp_output_cb(ppp_pcb *pcb, const void *data, u32_t len, void *ctx) {
    return uart_write_bytes(MODEM_UART_NUM, (const char *)data, len);
}


/**
 * Fungsi callback status PPP
 */
void ppp_status_cb(ppp_pcb *pcb, int err_code, void *ctx) {
    struct netif *pppif = (struct netif *)ctx;

    switch (err_code) {
        case PPPERR_NONE:
            ESP_LOGI(TAG, "PPP connected");
            ESP_LOGI(TAG, "IP address: %s", ipaddr_ntoa(&pppif->ip_addr));
            // Note: NAT/IP forwarding belum diaktifkan di sini
            break;
        default:
            ESP_LOGE(TAG, "PPP error code: %d", err_code);
            break;
    }
}

/**
 * Kirim perintah AT
 */
void modem_send_cmd(const char *cmd) {
    uart_write_bytes(MODEM_UART_NUM, cmd, strlen(cmd));
    uart_write_bytes(MODEM_UART_NUM, "\r\n", 2);
}

/**
 * Inisialisasi UART dan modem
 */
void init_uart_modem() {
    uart_config_t uart_config = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE
    };

    uart_driver_install(MODEM_UART_NUM, BUF_SIZE * 2, 0, 0, NULL, 0);
    uart_param_config(MODEM_UART_NUM, &uart_config);
    uart_set_pin(MODEM_UART_NUM, MODEM_TXD, MODEM_RXD, MODEM_RTS, MODEM_CTS);
}

/**
 * Konfigurasi modem dengan perintah AT
 */
void modem_setup() {
    ESP_LOGI(TAG, "Setting up modem with AT commands...");
    modem_send_cmd("AT");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("AT+CFUN=1");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("AT+CGATT=1");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("AT+CGDCONT=1,\"IP\",\"internet\"");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("ATD*99#");
    vTaskDelay(pdMS_TO_TICKS(3000));
}

/**
 * Task utama untuk menangani PPP
 */
void ppp_task(void *arg) {
    uint8_t *buf = malloc(BUF_SIZE);

    modem_setup();

    ESP_LOGI(TAG, "Starting PPP session...");
    ppp = pppapi_pppos_create(&ppp_netif, ppp_output_cb, ppp_status_cb, &ppp_netif);
    assert(ppp != NULL);

    ppp_set_default(ppp);
    ppp_set_auth(ppp, PPPAUTHTYPE_NONE, "", "");
    ppp_connect(ppp, 0);

    while (1) {
        int len = uart_read_bytes(MODEM_UART_NUM, buf, BUF_SIZE, pdMS_TO_TICKS(100));
        if (len > 0) {
            pppos_input_tcpip(ppp, buf, len);
        }
    }

    free(buf);
}

/**
 * Inisialisasi Wi-Fi Access Point
 */
void init_wifi_ap() {
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);
    esp_wifi_set_mode(WIFI_MODE_AP);

    wifi_config_t ap_config = {
        .ap = {
            .ssid = "ESP32_AP",
            .password = "esp32pass",
            .ssid_len = 0,
            .channel = 1,
            .max_connection = 4,
            .authmode = WIFI_AUTH_WPA_WPA2_PSK
        },
    };

    if (strlen((char *)ap_config.ap.password) == 0) {
        ap_config.ap.authmode = WIFI_AUTH_OPEN;
    }

    esp_wifi_set_config(WIFI_IF_AP, &ap_config);
    esp_wifi_start();
    ESP_LOGI(TAG, "Wi-Fi AP started. SSID: %s", ap_config.ap.ssid);
}


/**
 * Fungsi utama
 */
void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ap_netif = esp_netif_create_default_wifi_ap();
    init_wifi_ap();
    init_uart_modem();
     
    esp_netif_config_t ppp_cfg = ESP_NETIF_DEFAULT_PPP();
    ppp_netif_esp = esp_netif_new(&ppp_cfg);

    esp_netif_attach(ppp_netif_esp, &ppp_netif);

    xTaskCreate(ppp_task, "ppp_task", 4096, NULL, 5, NULL);
}
