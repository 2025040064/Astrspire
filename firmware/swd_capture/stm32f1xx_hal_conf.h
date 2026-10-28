#ifndef ASTRSPIRE_F1_HAL_CONF_H
#define ASTRSPIRE_F1_HAL_CONF_H
#define HAL_MODULE_ENABLED
#define HAL_CORTEX_MODULE_ENABLED
#define HAL_RCC_MODULE_ENABLED
#define HAL_FLASH_MODULE_ENABLED
#define HAL_GPIO_MODULE_ENABLED
#define HAL_PWR_MODULE_ENABLED
#define HSE_VALUE 8000000
#define HSI_VALUE 8000000
#define LSI_VALUE 40000
#define LSE_VALUE 32768
#define HSE_STARTUP_TIMEOUT 100
#define LSE_STARTUP_TIMEOUT 5000
#define VDD_VALUE 3300
#define TICK_INT_PRIORITY 15
#define USE_RTOS 0
#define PREFETCH_ENABLE 1
#include "stm32f1xx_hal_rcc.h"
#include "stm32f1xx_hal_gpio.h"
#include "stm32f1xx_hal_cortex.h"
#include "stm32f1xx_hal_flash.h"
#include "stm32f1xx_hal_pwr.h"
#define assert_param(expr) ((void)0)
#endif
