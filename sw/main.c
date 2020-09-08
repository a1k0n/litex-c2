#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <irq.h>
#include <uart.h>
#include <console.h>
#include <generated/csr.h>

static char *readstr(void)
{
	char c[2];
	static char s[64];
	static int ptr = 0;

	if(readchar_nonblock()) {
		c[0] = readchar();
		c[1] = 0;
		switch(c[0]) {
			case 0x7f:
			case 0x08:
				if(ptr > 0) {
					ptr--;
					putsnonl("\x08 \x08");
				}
				break;
			case 0x07:
				break;
			case '\r':
			case '\n':
				s[ptr] = 0x00;
				putsnonl("\n");
				ptr = 0;
				return s;
			default:
				if(ptr >= (sizeof(s) - 1))
					break;
				putsnonl(c);
				s[ptr] = c[0];
				ptr++;
				break;
		}
	}

	return NULL;
}

static char *get_token(char **str)
{
	char *c, *d;

	c = (char *)strchr(*str, ' ');
	if(c == NULL) {
		d = *str;
		*str = *str+strlen(*str);
		return d;
	}
	*c = 0;
	d = *str;
	*str = c+1;
	return d;
}

static void prompt(void)
{
	printf("c2>");
}

static void help(void)
{
	puts("Available commands:");
	puts("help                            - this command");
	puts("reboot                          - reboot CPU");
	puts("dump                            - dump SFRs");
	puts("live                            - live dump of SFRs");
}

static void reboot(void)
{
	ctrl_reset_write(1);
}

static void puthex(uint8_t x) {
  static const char hex[] = "0123456789abcdef";
  putchar(hex[x>>4]);
  putchar(hex[x&15]);
}

static int dump(void) {
  uint8_t buf[0x80];

  for (int i = 0x80; i < 0x100; i++) {
    c2_addr_write(i);
    c2_cmd_write(2);  // send address
    c2_cmd_write(1);  // read data
    // wait for read completion, or error
    for (;;) {
      uint8_t s = c2_stat_read();
      if (s & 0x80) {
        puts("\r\x1b[9B\n***** c.2 error ******");
        return 0;
      }
      if (s & 0x40) break;
    }
    buf[i-0x80] = c2_rxbuf_read();
  }

  for (int i = 0x80; i < 0x100; i++) {
    puthex(buf[i-0x80]);
    if ((i & 0x0f) == 0x0f) {
      puts("");
    } else {
      putsnonl(" ");
    }
  }
  return 1;
}

static void livedump(void) {
  for (;;) {
    if (!dump()) {
      break;
    }
    busy_wait(15);
    if (readchar_nonblock()) {
      // swallow the char that stopped the dump
      readchar();
      break;
    }
    putsnonl("\x1b[8A");
  }
}

static void console_service(void)
{
	char *str;
	char *token;

	str = readstr();
	if(str == NULL) return;
	token = get_token(&str);
	if(strcmp(token, "help") == 0)
		help();
	else if(strcmp(token, "reboot") == 0)
		reboot();
	else if(strcmp(token, "dump") == 0)
		dump();
	else if(strcmp(token, "live") == 0)
		livedump();
	prompt();
}

int main(void)
{
#ifdef CONFIG_CPU_HAS_INTERRUPT
	irq_setmask(0);
	irq_setie(1);
#endif
	uart_init();

	puts("\nc.2 interface test "__DATE__" "__TIME__"\n");
	help();
	prompt();

	while(1) {
		console_service();
	}

	return 0;
}
