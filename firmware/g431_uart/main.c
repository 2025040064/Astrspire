/* Independent G431RBT6 UART fixture. No Flash/option-byte/EEPROM writes.
 * USART1: PA9 TX / PA10 RX, 115200 8N1. Internal HSI16 after reset.
 * Commands are explicitly requested over UART; streaming is off at startup.
 */
#include "stm32g4xx_hal.h"
#include <string.h>

static UART_HandleTypeDef uart;
static uint8_t rx_byte;
static volatile uint8_t rx_ring[128];
static volatile uint32_t rx_head, rx_tail, rx_errors;
static uint8_t streaming;
static uint32_t sequence;

typedef struct {
    float values[16];
    uint8_t tail[4];
} SamplePacket;
typedef char PacketSizeMustBe68[(sizeof(SamplePacket) == 68) ? 1 : -1];

void SysTick_Handler(void) { HAL_IncTick(); }
void USART1_IRQHandler(void) { HAL_UART_IRQHandler(&uart); }
void _init(void) {}
void _fini(void) {}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *handle)
{
    uint32_t next;
    if (handle != &uart) { return; }
    next = (rx_head + 1) % sizeof(rx_ring);
    if (next != rx_tail) {
        rx_ring[rx_head] = rx_byte;
        __DMB();
        rx_head = next;
    } else {
        rx_errors++;
    }
    HAL_UART_Receive_IT(&uart, &rx_byte, 1);
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *handle)
{
    if (handle == &uart) {
        rx_errors++;
        HAL_UART_Receive_IT(&uart, &rx_byte, 1);
    }
}

static void transmit_text(const char *text)
{
    if (HAL_UART_Transmit(&uart, (const uint8_t *)text, strlen(text), 30) != HAL_OK) {
        streaming = 0;
    }
}

static void execute_command(char *command)
{
    if (strcmp(command, "ASTR:STOP") == 0) {
        streaming = 0;
    } else if (strcmp(command, "ASTR:START") == 0) {
        streaming = 1;
    } else if (!streaming && strcmp(command, "ASTR:HELLO") == 0) {
        transmit_text("ASTRSPIRE:G431_UART_V1,115200,16,10\r\n");
    } else if (!streaming && strncmp(command, "ASTR:PING:", 10) == 0) {
        transmit_text("PONG:");
        transmit_text(command + 10);
        transmit_text("\r\n");
    }
}

static void uart_init(void)
{
    GPIO_InitTypeDef pins = {0};
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART1_CLK_ENABLE();
    pins.Pin = GPIO_PIN_9 | GPIO_PIN_10;
    pins.Mode = GPIO_MODE_AF_PP;
    pins.Pull = GPIO_NOPULL;
    pins.Speed = GPIO_SPEED_FREQ_LOW;
    pins.Alternate = GPIO_AF7_USART1;
    HAL_GPIO_Init(GPIOA, &pins);
    uart.Instance = USART1;
    uart.Init.BaudRate = 115200;
    uart.Init.WordLength = UART_WORDLENGTH_8B;
    uart.Init.StopBits = UART_STOPBITS_1;
    uart.Init.Parity = UART_PARITY_NONE;
    uart.Init.Mode = UART_MODE_TX_RX;
    uart.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    uart.Init.OverSampling = UART_OVERSAMPLING_16;
    uart.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    uart.Init.ClockPrescaler = UART_PRESCALER_DIV1;
    if (HAL_UART_Init(&uart) != HAL_OK) { for (;;) {} }
    HAL_NVIC_SetPriority(USART1_IRQn, 1, 0);
    HAL_NVIC_EnableIRQ(USART1_IRQn);
    if (HAL_UART_Receive_IT(&uart, &rx_byte, 1) != HAL_OK) { for (;;) {} }
}

int main(void)
{
    char command[48];
    uint32_t command_length = 0, previous, now, channel;
    uint8_t dropping_command = 0, byte;
    SamplePacket packet = {{0}, {0x00, 0x00, 0x80, 0x7f}};
    SystemCoreClockUpdate();
    if (HAL_Init() != HAL_OK) { for (;;) {} }
    uart_init();
    previous = HAL_GetTick();
    for (;;) {
        while (rx_tail != rx_head) {
            byte = rx_ring[rx_tail];
            rx_tail = (rx_tail + 1) % sizeof(rx_ring);
            if (byte == '\n' || byte == '\r') {
                if (!dropping_command && command_length) {
                    command[command_length] = '\0';
                    execute_command(command);
                }
                command_length = 0;
                dropping_command = 0;
            } else if (!dropping_command) {
                if (byte < 32 || byte > 126 || command_length >= sizeof(command) - 1) {
                    command_length = 0;
                    dropping_command = 1;
                } else {
                    command[command_length++] = (char)byte;
                }
            }
        }
        now = HAL_GetTick();
        if (!streaming) { previous = now; continue; }
        if ((uint32_t)(now - previous) < 10) { continue; }
        previous = now;
        sequence++;
        packet.values[0] = (float)sequence;
        packet.values[1] = (float)now;
        for (channel = 2; channel < 15; channel++) {
            packet.values[channel] = (float)((int32_t)(sequence % 200) - 100) * (float)(channel - 1) / 100.0f;
        }
        packet.values[15] = (float)rx_errors;
        if (HAL_UART_Transmit(&uart, (uint8_t *)&packet, sizeof(packet), 20) != HAL_OK) {
            streaming = 0;
        }
    }
}
