#include <stdint.h>
#include <pru_cfg.h>
#include <pru_intc.h>
#include <rsc_types.h>
#include <pru_rpmsg.h>
#include "resource_table_empty.h"

/* McSPI1 Registers (AM335x) */
#define MCSPI1_BASE             0x481A0000
#define MCSPI_SYSCONFIG         (*(volatile uint32_t *)(MCSPI1_BASE + 0x110))
#define MCSPI_SYSSTATUS         (*(volatile uint32_t *)(MCSPI1_BASE + 0x114))
#define MCSPI_IRQSTATUS         (*(volatile uint32_t *)(MCSPI1_BASE + 0x118))
#define MCSPI_IRQENABLE         (*(volatile uint32_t *)(MCSPI1_BASE + 0x11C))
#define MCSPI_MODULCTRL         (*(volatile uint32_t *)(MCSPI1_BASE + 0x128))
#define MCSPI_CH0CONF           (*(volatile uint32_t *)(MCSPI1_BASE + 0x12C))
#define MCSPI_CH0STAT           (*(volatile uint32_t *)(MCSPI1_BASE + 0x130))
#define MCSPI_CH0CTRL           (*(volatile uint32_t *)(MCSPI1_BASE + 0x134))
#define MCSPI_TX0               (*(volatile uint32_t *)(MCSPI1_BASE + 0x138))
#define MCSPI_RX0               (*(volatile uint32_t *)(MCSPI1_BASE + 0x13C))

/* Control Module (Pin Mux) - PRU might not have access depending on configuration, 
   but host usually sets this up before starting PRU. */

#define PRU_SHARED_RAM_SIZE     (12 * 1024)

/* PRU Shared Memory */
#define PRU_SHARED_MEM_BASE     0x00012000
volatile uint8_t *shared_mem = (volatile uint8_t *)PRU_SHARED_MEM_BASE;

/* PRU Registers */
volatile register uint32_t __R30;
volatile register uint32_t __R31;

/* Whitebeet Handshake Constants */
#define HDR_SIZE_MASTER         0xAAAA
#define HDR_DATA_MASTER         0x55550000

/* SPI Helper Functions */
void spi_init() {
    /* Reset McSPI */
    MCSPI_SYSCONFIG = 0x01;
    while (!(MCSPI_SYSSTATUS & 0x01));

    /* Module control: Master mode, single channel */
    MCSPI_MODULCTRL = 0x00;

    /* Channel 0 config: 
       - Clock divider: 48MHz / (2^0) = 48MHz (actually depend on sys clk)
       - Polarity/Phase: Mode 0
       - Word length: 8-bit (0x07)
       - TRM defines many more bits.
    */
    MCSPI_CH0CONF = (0x07 << 7) | (1 << 16) | (1 << 18); // 8-bit, DPE0=1, IS=0 (adjust for wiring)
}

uint8_t spi_transfer_byte(uint8_t data) {
    MCSPI_TX0 = data;
    MCSPI_CH0CTRL |= 0x01; // Enable channel
    while (!(MCSPI_CH0STAT & 0x01)); // Wait for TX empty
    while (!(MCSPI_CH0STAT & 0x02)); // Wait for RX full
    uint8_t ret = MCSPI_RX0;
    MCSPI_CH0CTRL &= ~0x01; // Disable channel
    return ret;
}

void main(void) {
    /* Clear SYSCFG[STANDBY_MODE] and SYSCFG[IDLE_MODE] to enable OCP master port */
    CT_CFG.SYSCFG_bit.STANDBY_MODE = 0;
    CT_CFG.SYSCFG_bit.IDLE_MODE = 0;

    spi_init();

    while (1) {
        /* 1. Wait for Host to trigger a command */
        if (shared_mem[0] == 0 && shared_mem[1] == 1) {
            uint16_t master_size;
            uint16_t slave_size;
            uint16_t transfer_size;
            uint16_t i;
            uint8_t s_h, s_l;

            shared_mem[0] = 1; // Mark PRU as BUSY

            master_size = (shared_mem[2] << 8) | shared_mem[3];

            /* 2. Wait for SLAVE Ready (P9_23 -> R31 bit 1) */
            while (!(__R31 & (1 << 1)));

            /* 3. SIZE Handshake (0xAAAA) */
            spi_transfer_byte(0xAA);
            spi_transfer_byte(0xAA);
            s_h = spi_transfer_byte(shared_mem[2]); // Master size high
            s_l = spi_transfer_byte(shared_mem[3]); // Master size low
            
            slave_size = (s_h << 8) | s_l;
            shared_mem[4] = s_h;
            shared_mem[5] = s_l;

            /* 4. DATA Handshake (0x5555) */
            /* Wait for SLAVE Ready again */
            while (!(__R31 & (1 << 1)));

            spi_transfer_byte(0x55);
            spi_transfer_byte(0x55);
            spi_transfer_byte(0x00);
            spi_transfer_byte(0x00);

            transfer_size = (master_size > slave_size) ? master_size : slave_size;

            for (i = 0; i < transfer_size; i++) {
                uint8_t tx_byte = (i < master_size) ? shared_mem[6 + i] : 0x00;
                uint8_t rx = spi_transfer_byte(tx_byte);
                if (i < PRU_SHARED_RAM_SIZE - 6) {
                    shared_mem[6 + i] = rx;
                }
            }

            shared_mem[1] = 0; // Command complete
            shared_mem[0] = 0; // PRU IDLE
        }
    }
}
