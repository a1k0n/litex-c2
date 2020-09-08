SOC_DIRECTORY=$(HOME)/tmp/litex/litex/soc
BUILD_DIR=../build/orangecrab

include $(BUILD_DIR)/software/include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

OBJECTS=isr.o main.o

all: firmware.bin

# pull in dependency info for *existing* .o files
-include $(OBJECTS:.o=.d)

%.bin: %.elf
	$(OBJCOPY) -O binary $< $@
	chmod -x $@

firmware.elf: $(OBJECTS) linker.ld
	$(LD) $(LDFLAGS) \
		-T linker.ld \
		-N -o $@ \
		 $(BUILD_DIR)/software/libbase/crt0.o \
		$(OBJECTS) \
		-L$(BUILD_DIR)/software/libbase \
		-L$(BUILD_DIR)/software/libcompiler_rt \
		-lbase-nofloat \
		-lcompiler_rt

	chmod -x $@

%.o: %.c
	$(compile) -O2

%.o: %.S
	$(assemble)

clean:
	$(RM) $(OBJECTS) $(OBJECTS:.o=.d) firmware.elf firmware.bin .*~ *~

.PHONY: all clean load
