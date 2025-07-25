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
#include "lwip/tcpip.h"  
#include "netif/ppp/pppapi.h"
#include "netif/ppp/pppos.h"
#include "netif/ppp/ppp.h"
#include "lwip/priv/tcpip_priv.h" 
#include "lwip/dns.h"
#include "lwip/lwip_napt.h"

//#include "esp_netif_nat.h"


#define MODEM_UART_NUM UART_NUM_1
#define MODEM_TXD      (GPIO_NUM_17)
#define MODEM_RXD      (GPIO_NUM_18)
#define MODEM_RTS      (UART_PIN_NO_CHANGE)
#define MODEM_CTS      (UART_PIN_NO_CHANGE)
#define BUF_SIZE       (1024)

#ifndef PPPAUTHTYPE_NONE
#define PPPAUTHTYPE_NONE 0
#endif

#if IP_NAPT
#include "lwip/lwip_napt.h"
#endif


static const char *TAG = "PPP_AP";
static struct netif ppp_netif;
static ppp_pcb *ppp = NULL;
static esp_netif_t *ap_netif = NULL;
static esp_netif_t *ppp_netif_esp = NULL;


typedef struct {
    ppp_pcb *ppp;
} ppp_cfg_args_t;

/**
 * Fungsi callback output PPP (modem TX)
 */
u32_t ppp_output_cb(ppp_pcb *pcb, const void *data, u32_t len, void *ctx) {
    return uart_write_bytes(MODEM_UART_NUM, (const char *)data, len);
}

extern void ip_forwarding_enable(void);  // Deklarasi manual

void ip_forwarding_enable() {
    extern int ip_forward;  // LwIP global
    ip_forward = 1;
}
/**
 * Fungsi callback status PPP
 */

void ppp_status_cb(ppp_pcb *pcb, int err_code, void *ctx) {
    struct netif *pppif = (struct netif *)ctx;

    switch (err_code) {
        case PPPERR_NONE:
            ESP_LOGI(TAG, "PPP connected");
            ESP_LOGI(TAG, "IP: %s", ipaddr_ntoa(&pppif->ip_addr));
            ESP_LOGI(TAG, "Gateway: %s", ipaddr_ntoa(&pppif->gw));
            ESP_LOGI(TAG, "Netmask: %s", ipaddr_ntoa(&pppif->netmask));

            ip_addr_t dnsserver;
            IP_ADDR4(&dnsserver, 8, 8, 8, 8);
            dns_setserver(0, &dnsserver);  // index 0 = primary DNS
            netif_set_up(&ppp_netif);

            
           /* esp_err_t err = esp_netif_dhcps_stop(ap_netif); // Optional
            if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
                ESP_LOGW(TAG, "DHCP server stop failed: %s", esp_err_to_name(err));
            }*/

           #if IP_NAPT
           esp_netif_ip_info_t ip_info;
           if (esp_netif_get_ip_info(ap_netif, &ip_info) == ESP_OK) {
               u32_t ap_ip = ip4_addr_get_u32(&ip_info.ip);
               ip_napt_enable(ap_ip, 1);
              // ip_napt_enable(netif_ip4_addr(netif), 1);
               ESP_LOGI(TAG, "LwIP NAT enabled on softAP (%s)", ip4addr_ntoa((const ip4_addr_t *)&ip_info.ip));
           }
           #endif
           //esp_netif_t *ppp_netif = ...;  
           esp_netif_set_default_netif(ppp_netif_esp);  
           netif_set_default(&ppp_netif);
          // tcpip_callback(enable_ip_forwarding, NULL);  // Enable IP forwarding
           
            break;

        default:
            ESP_LOGE(TAG, "PPP error code: %d", err_code);
            break;
    }
}

/**
 * Fungsi konfigurasi PPP yang dipanggil di dalam konteks TCPIP
 */
static void ppp_setup_cb(void *arg) {
    ppp_cfg_args_t *cfg = (ppp_cfg_args_t *)arg;

    ESP_LOGI(TAG, "PPP setup in TCPIP thread");
    ppp_set_auth(cfg->ppp, PPPAUTHTYPE_NONE, "", "");
    ppp_set_default(cfg->ppp);
    netif_set_default(&ppp_netif);
    ppp_connect(cfg->ppp, 0);  

    free(cfg);
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
 * Tunggu respon CONNECT dari modem
 */
 
bool wait_for_connect_response() {
    char resp[256] = {0};
    int total_len = 0;

    for (int i = 0; i < 20; i++) {  // 20 * 500ms = 10 detik
        int len = uart_read_bytes(MODEM_UART_NUM, (uint8_t *)(resp + total_len), sizeof(resp) - total_len - 1, pdMS_TO_TICKS(500));
        if (len > 0) {
            total_len += len;
            resp[total_len] = '\0';
            ESP_LOGI(TAG, "Modem response so far: %s", resp);
            if (strstr(resp, "CONNECT")) {
                return true;
            }
        }
    }

    return false;
}

/**
 * Konfigurasi modem dengan perintah AT
 */
 
void modem_setup() {
    ESP_LOGI(TAG, "Setting up modem with AT commands...");
    modem_send_cmd("AT");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("ATE0");  // Disable echo (penting untuk mencegah loopback)
    vTaskDelay(pdMS_TO_TICKS(500));
    modem_send_cmd("AT+CFUN=1");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("AT+CREG?");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("AT+CSQ");
    vTaskDelay(pdMS_TO_TICKS(500));
    modem_send_cmd("AT+CGATT=1");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("AT+CGDCONT=1,\"IP\",\"internet\"");
    vTaskDelay(pdMS_TO_TICKS(1000));
    modem_send_cmd("ATD*99#");

    if (!wait_for_connect_response()) {
        ESP_LOGE(TAG, "Modem did not return CONNECT, aborting...");
        vTaskDelete(NULL);  // Stop the task if failed
    } else {
        ESP_LOGI(TAG, "Modem CONNECT received, ready for PPP.");
    }
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

    ppp_cfg_args_t *cfg = malloc(sizeof(ppp_cfg_args_t));
    cfg->ppp = ppp;
    tcpip_callback(ppp_setup_cb, cfg);

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
    
    
    esp_netif_ip_info_t ip_info;
    ip_info.ip.addr = ipaddr_addr("192.168.4.1");
    ip_info.gw.addr = ipaddr_addr("192.168.4.1");  // gateway
    ip_info.netmask.addr = ipaddr_addr("255.255.255.0");
    esp_netif_dhcps_stop(ap_netif);
    esp_netif_set_ip_info(ap_netif, &ip_info);
    esp_netif_dhcps_start(ap_netif);

    wifi_config_t ap_config = {
        .ap = {
            .ssid = "ESP32_AP",
            .password = "esp32pass",
            .ssid_len = 0,
            .channel = 1,
            .max_connection = 5,
            .authmode = WIFI_AUTH_WPA_WPA2_PSK,
            .ssid_hidden = 0,
            .beacon_interval = 100,
            .pairwise_cipher = WIFI_CIPHER_TYPE_CCMP
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
    
    esp_netif_dns_info_t dns;
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    dns.ip.u_addr.ip4.addr = ipaddr_addr("8.8.8.8");  // Bisa juga pakai 1.1.1.1
    esp_err_t err = esp_netif_set_dns_info(ap_netif, ESP_NETIF_DNS_MAIN, &dns);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set DNS for softAP: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "DNS 8.8.8.8 applied to SoftAP");
    }

    esp_netif_config_t ppp_cfg = ESP_NETIF_DEFAULT_PPP();
    ppp_netif_esp = esp_netif_new(&ppp_cfg);
    esp_netif_attach(ppp_netif_esp, &ppp_netif);
    esp_netif_set_default_netif(ppp_netif_esp);
    
    xTaskCreate(ppp_task, "ppp_task", 8192, NULL, 5, NULL);
}
