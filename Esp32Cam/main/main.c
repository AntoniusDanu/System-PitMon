#include <string.h>
#include <stdio.h>
#include <esp_log.h>
#include <esp_event.h>
#include <esp_netif.h>
#include <nvs_flash.h>
#include <esp_wifi.h>
#include <esp_camera.h>
#include <driver/gpio.h>
#include <esp_timer.h>
#include <esp_http_client.h>
#include <lwip/sockets.h>
#include <lwip/netdb.h>

#define WIFI_SSID       "BRT ELECTRIC GUEST"//"BRT Juken"
#define WIFI_PASS       "Transformasi2027"//"A1b2c3d4e5"
#define TAG "ESP32-CAM"
#define LED_GPIO        GPIO_NUM_4
#define PIT_ID          0  // Ganti sesuai ID PIT (0=P1, 1=P2, dst)
#define BACKEND_FMT     "http://167.172.79.82:8000/upload"      //Ganti BACKEND_URL atau BACKEND_FMT

#define PWDN_GPIO_NUM   32
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM   26
#define SIOC_GPIO_NUM   27
#define Y9_GPIO_NUM     35
#define Y8_GPIO_NUM     34
#define Y7_GPIO_NUM     39
#define Y6_GPIO_NUM     36
#define Y5_GPIO_NUM     21
#define Y4_GPIO_NUM     19
#define Y3_GPIO_NUM     18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM  25
#define HREF_GPIO_NUM   23
#define PCLK_GPIO_NUM   22

const char *ngrok_root_cert = \
"-----BEGIN CERTIFICATE-----\n" \
"MIIEVzCCAj+gAwIBAgIRALBXPpFzlydw27SHyzpFKzgwDQYJKoZIhvcNAQELBQAw\n" \
"TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh\n" \
"cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMjQwMzEzMDAwMDAw\n" \
"WhcNMjcwMzEyMjM1OTU5WjAyMQswCQYDVQQGEwJVUzEWMBQGA1UEChMNTGV0J3Mg\n" \
"RW5jcnlwdDELMAkGA1UEAxMCRTYwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAATZ8Z5G\n" \
"h/ghcWCoJuuj+rnq2h25EqfUJtlRFLFhfHWWvyILOR/VvtEKRqotPEoJhC6+QJVV\n" \
"6RlAN2Z17TJOdwRJ+HB7wxjnzvdxEP6sdNgA1O1tHHMWMxCcOrLqbGL0vbijgfgw\n" \
"gfUwDgYDVR0PAQH/BAQDAgGGMB0GA1UdJQQWMBQGCCsGAQUFBwMCBggrBgEFBQcD\n" \
"ATASBgNVHRMBAf8ECDAGAQH/AgEAMB0GA1UdDgQWBBSTJ0aYA6lRaI6Y1sRCSNsj\n" \
"v1iU0jAfBgNVHSMEGDAWgBR5tFnme7bl5AFzgAiIyBpY9umbbjAyBggrBgEFBQcB\n" \
"AQQmMCQwIgYIKwYBBQUHMAKGFmh0dHA6Ly94MS5pLmxlbmNyLm9yZy8wEwYDVR0g\n" \
"BAwwCjAIBgZngQwBAgEwJwYDVR0fBCAwHjAcoBqgGIYWaHR0cDovL3gxLmMubGVu\n" \
"Y3Iub3JnLzANBgkqhkiG9w0BAQsFAAOCAgEAfYt7SiA1sgWGCIpunk46r4AExIRc\n" \
"MxkKgUhNlrrv1B21hOaXN/5miE+LOTbrcmU/M9yvC6MVY730GNFoL8IhJ8j8vrOL\n" \
"pMY22OP6baS1k9YMrtDTlwJHoGby04ThTUeBDksS9RiuHvicZqBedQdIF65pZuhp\n" \
"eDcGBcLiYasQr/EO5gxxtLyTmgsHSOVSBcFOn9lgv7LECPq9i7mfH3mpxgrRKSxH\n" \
"pOoZ0KXMcB+hHuvlklHntvcI0mMMQ0mhYj6qtMFStkF1RpCG3IPdIwpVCQqu8GV7\n" \
"s8ubknRzs+3C/Bm19RFOoiPpDkwvyNfvmQ14XkyqqKK5oZ8zhD32kFRQkxa8uZSu\n" \
"h4aTImFxknu39waBxIRXE4jKxlAmQc4QjFZoq1KmQqQg0J/1JF8RlFvJas1VcjLv\n" \
"YlvUB2t6npO6oQjB3l+PNf0DpQH7iUx3Wz5AjQCi6L25FjyE06q6BZ/QlmtYdl/8\n" \
"ZYao4SRqPEs/6cAiF+Qf5zg2UkaWtDphl1LKMuTNLotvsX99HP69V2faNyegodQ0\n" \
"LyTApr/vT01YPE46vNsDLgK+4cL6TrzC/a4WcmF5SRJ938zrv/duJHLXQIku5v0+\n" \
"EwOy59Hdm0PT/Er/84dDV0CSjdR/2XuZM3kpysSKLgD1cKiDA+IRguODCxfO9cyY\n" \
"Ig46v9mFmBvyH04=\n" \
"-----END CERTIFICATE-----\n";

static EventGroupHandle_t wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0


static void init_led(void) {
    gpio_config_t io_conf = {
        .pin_bit_mask = 1ULL << LED_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&io_conf);
    gpio_set_level(LED_GPIO, 1); 
}

static void blink_led_success(void) {
    gpio_set_level(LED_GPIO, 1);
    vTaskDelay(pdMS_TO_TICKS(100));
    gpio_set_level(LED_GPIO, 0);
}

static void blink_led_error(void) {
    for (int i = 0; i < 2; ++i) {
        gpio_set_level(LED_GPIO, 1);
        vTaskDelay(pdMS_TO_TICKS(200));
        gpio_set_level(LED_GPIO, 0);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

static void test_dns_resolution() {
    struct hostent *he = gethostbyname("167.172.79.82");
    if (he == NULL) {
        ESP_LOGW(TAG, " DNS lookup failed (gethostbyname)");
    } else {
        ESP_LOGI(TAG, " DNS lookup success. IP: %s", inet_ntoa(*(struct in_addr*)he->h_addr));
    }
}

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } 
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi terputus. Mencoba reconnect...");
        esp_wifi_connect();
    } 
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/*
static void connect_wifi(void) {
    wifi_event_group = xEventGroupCreate();

    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_t *netif = esp_netif_create_default_wifi_sta();

    // Pastikan DHCP aktif
    esp_netif_dhcpc_stop(netif);  // optional safe reset
    esp_netif_dhcpc_start(netif);

    // WiFi init
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };

    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL);

    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();

    // Tunggu koneksi WiFi
    EventBits_t bits = xEventGroupWaitBits(wifi_event_group,
                                           WIFI_CONNECTED_BIT,
                                           pdFALSE,
                                           pdFALSE,
                                           pdMS_TO_TICKS(10000));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "WiFi connected (DHCP)");
        blink_led_success();
    } else {
        ESP_LOGE(TAG, "WiFi connection failed");
        blink_led_error();
    }
}*/


static void connect_wifi(void) {
    wifi_event_group = xEventGroupCreate();

    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_t *netif = esp_netif_create_default_wifi_sta();

    // IP statis
    esp_netif_dhcpc_stop(netif);  // stop DHCP

    // IP, gateway, dan netmask hotspot vivo
    ip4_addr_t ip, gw, netmask;
  /*  ip4addr_aton("192.168.234.50", &ip);      // IP statis ESP32 (pilih yang belum dipakai)
    ip4addr_aton("192.168.234.181", &gw);     // Gateway = IP hotspot HP
    ip4addr_aton("255.255.255.0", &netmask);  // Netmask dari subnet */

    ip4addr_aton("10.10.12.50", &ip);     // ganti IP statik ESP32
    ip4addr_aton("10.10.12.1", &gw);      // gateway, wifi guest
    ip4addr_aton("255.255.255.0", &netmask);// 
    
    esp_netif_ip_info_t ip_info;
    ip_info.ip.addr = ip.addr;
    ip_info.gw.addr = gw.addr;
    ip_info.netmask.addr = netmask.addr;

    esp_netif_set_ip_info(netif, &ip_info); 

    // DNS
    esp_netif_dns_info_t dns;
    dns.ip.type = IPADDR_TYPE_V4;
    dns.ip.u_addr.ip4.addr = inet_addr("8.8.8.8");
    esp_netif_set_dns_info(netif, ESP_NETIF_DNS_MAIN, &dns);

    // WiFi init
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };

    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL);

    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();

    // Tunggu koneksi WiFi
    EventBits_t bits = xEventGroupWaitBits(wifi_event_group,
                                           WIFI_CONNECTED_BIT,
                                           pdFALSE,
                                           pdFALSE,
                                           pdMS_TO_TICKS(10000));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, " WiFi connected.");
        ESP_LOGI(TAG, " Static IP Address: " IPSTR, IP2STR(&ip_info.ip));
        gpio_set_level(LED_GPIO, 0); // turn off LED if connected
    } else {
        ESP_LOGE(TAG, " WiFi connection failed");
        blink_led_error();
    }
} 

esp_err_t upload_image(uint8_t *image_buf, size_t image_len, int pit_id) {
    char url[256];
    snprintf(url, sizeof(url), BACKEND_FMT "?pit=%d", pit_id); //ganti BACKEND_URL atau BACKEND_FMT
    ESP_LOGI(TAG, "Upload URL: %s", url);

    const char *boundary = "----esp32boundary";
    char start_part[256];
    char end_part[64];

    snprintf(start_part, sizeof(start_part),
        "--%s\r\n"
        "Content-Disposition: form-data; name=\"file\"; filename=\"image.jpg\"\r\n"
        "Content-Type: image/jpeg\r\n\r\n", boundary);
    snprintf(end_part, sizeof(end_part), "\r\n--%s--\r\n", boundary);

    size_t total_len = strlen(start_part) + image_len + strlen(end_part);


    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 10000,
        .transport_type = HTTP_TRANSPORT_OVER_TCP,
      //  .cert_pem = ngrok_root_cert,                               
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);

    char content_type[64];
    snprintf(content_type, sizeof(content_type), "multipart/form-data; boundary=%s", boundary);
    esp_http_client_set_header(client, "Content-Type", content_type);
    esp_http_client_set_method(client, HTTP_METHOD_POST);
  //  esp_http_client_set_header(client, "Host", "2018-103-20-188-202.ngrok-free.app");

    esp_err_t err = esp_http_client_open(client, total_len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP open failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return err;
    }

    esp_http_client_write(client, start_part, strlen(start_part));
    esp_http_client_write(client, (char *)image_buf, image_len);
    esp_http_client_write(client, end_part, strlen(end_part));

    err = esp_http_client_perform(client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP perform failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return err;
    }

    int status = esp_http_client_get_status_code(client);
    ESP_LOGI(TAG, "HTTP status = %d", status);
    if (status != 200) {
        ESP_LOGW(TAG, "Upload failed, status = %d", status);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    esp_http_client_cleanup(client);
    return ESP_OK;
}

static esp_err_t init_camera(void) {
    camera_config_t config = {
        .pin_pwdn       = PWDN_GPIO_NUM,
        .pin_reset      = RESET_GPIO_NUM,
        .pin_xclk       = XCLK_GPIO_NUM,
        .pin_sccb_sda   = SIOD_GPIO_NUM,
        .pin_sccb_scl   = SIOC_GPIO_NUM,
        .pin_d7         = Y9_GPIO_NUM,
        .pin_d6         = Y8_GPIO_NUM,
        .pin_d5         = Y7_GPIO_NUM,
        .pin_d4         = Y6_GPIO_NUM,
        .pin_d3         = Y5_GPIO_NUM,
        .pin_d2         = Y4_GPIO_NUM,
        .pin_d1         = Y3_GPIO_NUM,
        .pin_d0         = Y2_GPIO_NUM,
        .pin_vsync      = VSYNC_GPIO_NUM,
        .pin_href       = HREF_GPIO_NUM,
        .pin_pclk       = PCLK_GPIO_NUM,
        .xclk_freq_hz   = 20000000,
        .ledc_timer     = LEDC_TIMER_0,
        .ledc_channel   = LEDC_CHANNEL_0,
        .pixel_format   = PIXFORMAT_JPEG,
        .frame_size     = FRAMESIZE_UXGA,
        .jpeg_quality   = 12,
        .fb_count       = 2,
    };

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera Init Failed: %s", esp_err_to_name(err));
        return err;
    }

    sensor_t *s = esp_camera_sensor_get();
    s->set_vflip(s, 1);
    s->set_hmirror(s, 1);
    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, 0);
    return ESP_OK;
}

static void capture_and_upload_task(void *pvParameters) {
    while (1) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb) {
            ESP_LOGE(TAG, "Failed to capture image");
            blink_led_error();
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }

        if (upload_image(fb->buf, fb->len, PIT_ID) == ESP_OK) {
            ESP_LOGI(TAG, "Upload sukses");
            blink_led_success();
        } else {
            ESP_LOGE(TAG, "Upload gagal");
            blink_led_error();
        }

        esp_camera_fb_return(fb);
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

static void task_heartbeat(void *pvParameters) {
    while (1) {
        esp_http_client_config_t config = {
            .url = "http://167.172.79.82:8000/heartbeat",
            .method = HTTP_METHOD_POST,
            .timeout_ms = 5000,
        };

        esp_http_client_handle_t client = esp_http_client_init(&config);
        esp_http_client_set_header(client, "Content-Type", "application/x-www-form-urlencoded");

        const char *post_data = "pit_id=PIT1";  // nama PIT
        esp_http_client_set_post_field(client, post_data, strlen(post_data));

        esp_err_t err = esp_http_client_perform(client);
        if (err == ESP_OK) {
            ESP_LOGI("HEARTBEAT", "Terkirim: %d", esp_http_client_get_status_code(client));
        } else {
            ESP_LOGW("HEARTBEAT", "Gagal: %s", esp_err_to_name(err));
        }

        esp_http_client_cleanup(client);
        vTaskDelay(pdMS_TO_TICKS(30000)); 
    }
}

void app_main(void) {
    nvs_flash_init();
    init_led();
    connect_wifi();
    test_dns_resolution();
    init_camera();
    xTaskCreate(capture_and_upload_task, "capture_and_upload_task", 8192, NULL, 5, NULL);
    xTaskCreate(task_heartbeat, "task_heartbeat", 4096, NULL, 4, NULL);

}
