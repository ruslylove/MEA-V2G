# Makefile for PRU firmware

# Adjust these paths for current BBB Debian image
PRU_CGT := /usr/share/ti/cgt-pru
PRU_SUPPORT := /usr/lib/ti/pru-software-support-package

# Project name
GEN_DIR := gen
TARGET := spi_whitebeet.out
LINKER_COMMAND_FILE := $(PRU_SUPPORT)/examples/am335x/PRU_gpioToggle/AM335x_PRU.cmd

# Compiler and Linker flags
# Note: clpru is in /usr/bin, so we use it directly.
# Includes need to point to /usr/include (part of ti-pru-cgt-installer).
CFLAGS := -v3 -O2 --display_error_number --endian=little --hardware_mac=on --obj_directory=$(GEN_DIR) --pp_directory=$(GEN_DIR) -ppd -ppa --asm_directory=$(GEN_DIR)
INCLUDE := -I$(PRU_SUPPORT)/include -I$(PRU_SUPPORT)/include/am335x -I$(PRU_CGT)/include
LNK_FLAGS := -cr -i$(PRU_CGT)/lib

# Source files
SRC := spi_whitebeet.c
OBJ := $(GEN_DIR)/spi_whitebeet.obj

.PHONY: all clean deploy

all: $(GEN_DIR) $(TARGET)

$(GEN_DIR):
	mkdir -p $(GEN_DIR)

$(GEN_DIR)/%.obj: %.c
	clpru --include_path=$(PRU_CGT)/include $(INCLUDE) $(CFLAGS) -c $< -fo $@

$(TARGET): $(OBJ)
	clpru $(CFLAGS) -z $(LNK_FLAGS) -o $@ $^ $(LINKER_COMMAND_FILE)

clean:
	rm -rf $(GEN_DIR) $(TARGET)

deploy: $(TARGET)
	cp $(TARGET) /lib/firmware/
	echo $(TARGET) > /sys/class/remoteproc/remoteproc1/firmware
	echo stop > /sys/class/remoteproc/remoteproc1/state 2>/dev/null || true
	echo start > /sys/class/remoteproc/remoteproc1/state
