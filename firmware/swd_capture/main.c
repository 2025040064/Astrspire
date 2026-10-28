/* Astrspire independent HAL SWD test source. No UART, USB or GPIO outputs. */
#if defined(STM32F103xB)
#include "stm32f1xx_hal.h"
#define CAPTURE_DEVICE_ID 0x410
#else
#include "stm32g4xx_hal.h"
#define CAPTURE_DEVICE_ID 0x468
#endif

typedef struct {
    volatile uint32_t guard;
    volatile uint32_t sequence;
    volatile uint32_t tick_ms;
    volatile float values[16];
} CaptureSlot;

typedef struct {
    volatile uint32_t magic, version, device_id, capacity, channels, period_ms, boot, write_sequence;
    CaptureSlot slots[64];
} CaptureArea;

volatile CaptureArea astrspire_capture;
static volatile uint32_t boot_cookie[2] __attribute__((section(".noinit")));

void SysTick_Handler(void) { HAL_IncTick(); }
void _init(void) {}
void _fini(void) {}

int main(void)
{
    uint32_t previous, now, sequence, channel, boot;
    volatile CaptureSlot *slot;
    SystemCoreClockUpdate();
    if (HAL_Init() != HAL_OK) { for (;;) {} }
    boot = boot_cookie[0] == 0x41534254 ? boot_cookie[1] + 1 : 1;
    boot_cookie[0] = 0x41534254;
    boot_cookie[1] = boot;
    astrspire_capture.version = 1;
    astrspire_capture.device_id = CAPTURE_DEVICE_ID;
    astrspire_capture.capacity = 64;
    astrspire_capture.channels = 16;
    astrspire_capture.period_ms = 10;
    astrspire_capture.boot = boot;
    astrspire_capture.write_sequence = 0;
    __DMB();
    astrspire_capture.magic = 0x41535744;
    previous = HAL_GetTick();
    for (;;) {
        now = HAL_GetTick();
        if ((uint32_t)(now - previous) < 10) { continue; }
        previous = now;
        sequence = astrspire_capture.write_sequence + 1;
        slot = &astrspire_capture.slots[sequence % 64];
        slot->guard = sequence * 2 + 1;
        __DMB();
        slot->sequence = sequence;
        slot->tick_ms = now;
        slot->values[0] = (float)sequence;
        slot->values[1] = (float)now;
        for (channel = 2; channel < 16; channel++) {
            slot->values[channel] = (float)((int32_t)(sequence % 200) - 100) * (float)(channel - 1) / 100.0f;
        }
        __DMB();
        slot->guard = sequence * 2;
        __DMB();
        astrspire_capture.write_sequence = sequence;
    }
}
