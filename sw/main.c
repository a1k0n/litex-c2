#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <irq.h>
#include <uart.h>
#include <console.h>
#include <generated/csr.h>

static char *readstr(void) {
  char        c[2];
  static char s[64];
  static int  ptr = 0;

  if (readchar_nonblock()) {
    c[0] = readchar();
    c[1] = 0;
    switch (c[0]) {
      case 0x7f:
      case 0x08:
        if (ptr > 0) {
          ptr--;
          putsnonl("\x08 \x08");
        }
        break;
      case 3:  // ctrl-C
        ptr = 0;
        s[0] = 0;
        putsnonl("\n");
        return s;
      case 0x15:  // ctrl-U
        ptr = 0;
        s[0] = 0;
        putsnonl("\r\x1b[K");
        return s;
      case '\r':
      case '\n':
        s[ptr] = 0x00;
        putsnonl("\n");
        ptr = 0;
        return s;
      default:
        // ignore any unhandled control characters
        if (c[0] < 32) {
          break;
        }
        if (ptr >= (sizeof(s) - 1)) break;
        putsnonl(c);
        s[ptr] = c[0];
        ptr++;
        break;
    }
  }

  return NULL;
}

static char *get_token(char **str) {
  char *c, *d;

  c = (char *)strchr(*str, ' ');
  if (c == NULL) {
    d    = *str;
    *str = *str + strlen(*str);
    return d;
  }
  *c   = 0;
  d    = *str;
  *str = c + 1;
  return d;
}

static void prompt(void) { printf("c2>"); }

static void help(void) {
  puts("Available commands:");
  puts("help                            - this command");
  puts("reboot                          - reboot CPU");
  puts("reset                           - reset target device");
  puts("dump                            - dump SFRs");
  puts("live                            - live dump of SFRs");
  puts("getreg <addr>                   - get value of SFR");
  puts("setreg <addr> <value>           - set value of SFR");
}

static void reboot(void) { ctrl_reset_write(1); }

static inline void wait_ready(void) {
  while (c2_cmd_read() != 0) {
  }
}

static int c2_writedata(uint8_t value) {
  wait_ready();
  c2_txdat_write(value);
  c2_cmd_write(4);  // write data
  return 1;
}

static int c2_readdata(uint8_t *value) {
  wait_ready();
  c2_cmd_write(1);               // read data
  for (;;) {
    uint8_t stat = c2_stat_read();
    if (stat & 0x40) {  // read success?
      *value = c2_rxbuf_read();
      return 1;
    }
    if (stat & 0x80) {  // error?  shouldn't happen
      return 0;
    }
  }
}

static int c2_writereg(uint8_t addr, uint8_t value) {
  wait_ready();
  c2_txdat_write(addr);
  c2_cmd_write(2);  // write address
  return c2_writedata(value);
}

static int c2_readreg(uint8_t addr, uint8_t *value) {
  wait_ready();
  c2_txdat_write(addr);
  c2_cmd_write(2);               // write address
  return c2_readdata(value);
}

static uint8_t c2_readaddr(void) {
  while (c2_cmd_read() != 0) {}  // make sure no other commands are waiting
  c2_cmd_write(3);  // address read command
  for (;;) {
    uint8_t stat = c2_stat_read();
    if (stat & 0x40) {  // read success?
      return c2_rxbuf_read();
    }
    if (stat & 0x80) {  // error?  shouldn't happen
      return 0xff;
    }
  }
}

static void puthex(char *out, uint8_t x) {
  static const char hex[] = "0123456789abcdef";
  out[0]                  = hex[x >> 4];
  out[1]                  = hex[x & 15];
}

static int gethex(const char *str, unsigned *val) {
  *val = 0;
  if (*str == 0) {
    // empty string is invalid
    return 0;
  }
  for (;;) {
    char c = *str++;
    if (c >= '0' && c <= '9') {
      *val = (*val << 4) + (c - '0');
    } else if (c >= 'a' && c <= 'f') {
      *val = (*val << 4) + (c + 10 - 'a');
    } else if (c >= 'A' && c <= 'F') {
      *val = (*val << 4) + (c + 10 - 'A');
    } else if ((c == 'x' || c == 'X') && *val == 0) {
      // discard "0x"
      // nothing to do here
    } else if (c == 0 || c == ' ' || c == '\n' || c == '\t' || c == '\r') {
      return 1;
    } else {
      return 0;
    }
  }
}

static void reset_target(void) {
  c2_cmd_write(5);  // reset target command
  wait_ready();     // wait for reset to be processed
  while (!(c2_stat_read() & 1)) {
    // busy-wait for reset to finish
  }
}

static int poll_inbusy(void) {
  for (int n = 1024; n > 0; n--) {
    uint8_t st = c2_readaddr();
    if ((st & 2) == 0) {  // !inbusy
      return 1;
    }
  }
  puts("poll_inbusy: response fail");
  return 0;
}

static int poll_outready(void) {
  for (int n = 1024; n > 0; n--) {
    uint8_t st = c2_readaddr();
    if ((st & 3) == 1) {  // !inbusy & outready
      return 1;
    }
  }
  puts("poll_outready: inbusy/outready response fail");
  return 0;
}

static void init_fpctl(int wait) {
  reset_target();
  // FPCTL init sequance
  c2_writereg(2, 2);
  // c2_writereg(2, 4);  // ?
  c2_writereg(2, 1);
  busy_wait(wait);  // wait _20 ms_?!
}

static int read_flash(uint16_t addr, int len) {
  c2_writereg(0xb4, 6);  // fpdat <- 6 (block read)
  // c2_writereg(0xb4, 0x0b);  // fpdat <- 0x0b (indirect read)
  if (!poll_outready()) {
    return 0;
  }

  uint8_t stat;
  if (!c2_readdata(&stat)) {
    return 0;
  }

  if (stat != 0x0d) {
    return 0;
  }
  c2_writedata(addr >> 8);
  if (!poll_inbusy()) {
    return 0;
  }
  c2_writedata(addr & 0xff);
  if (!poll_inbusy()) {
    return 0;
  }
  c2_writedata(len & 0xff);  // length
  if (!poll_outready()) {
    return 0;
  }
  if (!c2_readdata(&stat)) {
    return 0;
  }
  if (stat != 0x0d) {
    return 0;
  }

  for (int i = 0; i < len; i++) {
    if (!poll_outready()) {
      return 0;
    }
    c2_readdata(&stat);
    printf("%04x: %02x\n", addr, stat);
    addr++;
  }

  return 1;
}

static int dump(void) {
  uint8_t buf[0x80];
  char    sendbuf[0x40];

  const uint8_t addr0 = 0x80;

  uint8_t addr = addr0;
  wait_ready();
  c2_txdat_write(addr++);
  c2_cmd_write(2);  // send address
  for (int i = 0; i < 0x80; i++) {
    wait_ready();
    c2_cmd_write(1);  // read data
    c2_txdat_write(addr++);
    wait_ready();
    c2_cmd_write(2);  // send address
    // wait for read completion, or error
    int retry = 255;
    for (; retry > 0; retry--) {
      // unroll status check loop so we can hit it during the pipelined address
      // write so that the C2 bus stays nearly 100% active
      uint8_t s = c2_stat_read();
      if (s & 0x40) break;
      s = c2_stat_read();
      if (s & 0x40) break;
      s = c2_stat_read();
      if (s & 0x40) break;
      s = c2_stat_read();
      if (s & 0x40) break;
      if (s & 0x80) {
        puts("\r\x1b[9B\n***** c.2 error ******");
        return 0;
      }
      if (s & 0x40) break;
    }
    if (retry == 0) {
      goto dumperr;
    }
    buf[i] = c2_rxbuf_read();
  }

  char *o = sendbuf;
  for (int i = 0; i < 0x80; i++) {
    uint8_t col = i&0x0f;
    if (col == 0) {
      puthex(o, addr0 + i);
      o[2] = ':';
      o[3] = ' ';
      o += 4;
    }
    puthex(o, buf[i]);
    o[2] = col == 7 ? '|' : ' ';
    if (col == 15) {
      o[2] = '\n';
      o[3] = 0;
      o    = sendbuf;
      putsnonl(o);
    } else {
      o += 3;
    }
  }

  return 1;

dumperr:
  printf("dump fail: cmd %02x stat %02x\n", c2_cmd_read(), c2_stat_read());
  return 0;
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

static void console_service(void) {
  char *str;
  char *token;

  str = readstr();
  if (str == NULL) return;
  if (str[0] == 0) {
    // empty command, just repeat prompt
    prompt();
    return;
  }
  token = get_token(&str);
  if (strcmp(token, "help") == 0) {
    help();
  } else if (strcmp(token, "reboot") == 0) {
    reboot();
  } else if (strcmp(token, "dump") == 0) {
    dump();
  } else if (strcmp(token, "live") == 0) {
    livedump();
  } else if (strcmp(token, "reset") == 0) {
    uint8_t devid;
    reset_target();
    if (c2_readreg(0, &devid)) {
      printf("target reset; device id %02x\n", devid);
    } else {
      printf("target reset; no response\n");
    }
  } else if (strcmp(token, "readaddr") == 0) {
    printf("c2 address: %02x\n", c2_readaddr());
  } else if (strcmp(token, "readflash") == 0) {
    init_fpctl(20);
    for (int i = 0; i < 0x10000; i++) {
      read_flash(i, 256);
      if ((i & 0xff) == 0) {
        printf("\r%04x...", i);
      }
    }
  } else if (strcmp(token, "getreg") == 0) {
    unsigned addr;
    uint8_t value = 0;
    token = get_token(&str);
    if (gethex(token, &addr)) {
      int ok = c2_readreg(addr, &value);
      if (ok)  {
        printf("reg %02x: %02x\n", addr, value);
      } else {
        printf("reg %02x: read error\n", addr);
      }
    } else {
      puts("usage: getreg <regaddr hex>");
    }
  } else if (strcmp(token, "setreg") == 0) {
    const char *token1 = get_token(&str);
    const char *token2 = get_token(&str);
    unsigned addr, value;
    if (gethex(token1, &addr) && gethex(token2, &value)) {
      int ok = c2_writereg(addr, value);
      printf("reg %02x -> %02x: %d\n", addr, value, ok);
    } else {
      puts("usage: setreg <regaddr> <value>");
    }
  } else {
    puts("invalid command");
  }
  prompt();
}

extern unsigned int _edata_rom;

int main(void) {
#ifdef CONFIG_CPU_HAS_INTERRUPT
  irq_setmask(0);
  irq_setie(1);
#endif
  uart_init();

  printf(".data: %p\n", &_edata_rom);

  puts(
      "\nc.2 interface test "__DATE__
      " "__TIME__
      "\n");
  help();
  prompt();

  while (1) {
    console_service();
  }

  return 0;
}
