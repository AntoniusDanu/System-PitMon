#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "driver/gpio.h"

#define MODEM_UART_NUM UART_NUM_1
#define MODEM_TXD      (GPIO_NUM_17)
#define MODEM_RXD      (GPIO_NUM_18)
#define BUF_SIZE       1024

static const char *TAG = "AT_UART";

// Daftar AT command untuk diuji
const char *at_commands[] = {
    "ATE0\r\n",         // Nonaktifkan echo
    "ATI\r\n",          // Info modem
    "AT+CSQ\r\n",       // Cek sinyal
    "AT+CGATT?\r\n",    // Cek attach status
    "AT+COPS?\r\n",     // Cek operator jaringan
    "AT+CPIN?\r\n",
    "AT+CGDCONT?\r\n",
};

void send_at_command(const char *cmd) {
    ESP_LOGI(TAG, "Kirim AT: %s", cmd);
    uart_write_bytes(MODEM_UART_NUM, cmd, strlen(cmd));
}

void read_response() {
    uint8_t response[BUF_SIZE];
    int len = uart_read_bytes(MODEM_UART_NUM, response, BUF_SIZE - 1, pdMS_TO_TICKS(3000));
    if (len > 0) {
        response[len] = '\0';
        ESP_LOGI(TAG, "Respons Modem:\n%s", (char *)response);
    } else {
        ESP_LOGW(TAG, "Tidak ada respons");
    }
}

void app_main(void) {
    const uart_config_t uart_config = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE
    };

    uart_driver_install(MODEM_UART_NUM, BUF_SIZE * 2, 0, 0, NULL, 0);
    uart_param_config(MODEM_UART_NUM, &uart_config);
    uart_set_pin(MODEM_UART_NUM, MODEM_TXD, MODEM_RXD, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);

    // Escape dari mode PPP ke command mode
    ESP_LOGI(TAG, "Tunggu sebelum kirim escape +++");
    vTaskDelay(pdMS_TO_TICKS(1100));
    uart_write_bytes(MODEM_UART_NUM, "+++", 3);
    ESP_LOGI(TAG, "Kirim escape: +++");
    vTaskDelay(pdMS_TO_TICKS(1100));

    // Kirim perintah AT satu per satu
    for (int i = 0; i < sizeof(at_commands)/sizeof(at_commands[0]); i++) {
        send_at_command(at_commands[i]);
        read_response();
        vTaskDelay(pdMS_TO_TICKS(2000));  // jeda antar perintah
    }

    ESP_LOGI(TAG, "Tes selesai, masuk ke loop monitoring");

    // Loop terus kirim AT setiap 5 detik
    while (1) {
        send_at_command("AT\r\n");
        read_response();
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

