from migen import Module, TSTriple, Array, Signal, Cat, FSM, If, NextState, NextValue
from litex.soc.interconnect.csr import AutoCSR, CSRStorage, CSRStatus


class C2Interface(Module, AutoCSR):
    def __init__(self, c2):

        txwidth = 13
        txlen = Signal(max=txwidth+1)
        txbuf = Signal(txwidth)
        rxbuf = Signal(8)
        rxlen = Signal(4)

        waitlen = Signal(7)
        rfull = Signal()
        readerror = Signal()

        reset_count = Signal(max=961)

        self._cmd = CSRStorage(8)
        self._stat = CSRStatus(8)
        self._rxbuf = CSRStatus(8)
        self.comb += self._rxbuf.status.eq(rxbuf)
        self._txdat = CSRStorage(8)

        c2d = TSTriple()
        c2ck = Signal(reset=1)
        self.comb += c2.c2ck.eq(c2ck)
        self.specials += c2d.get_tristate(c2.c2d)

        self._pwcon = CSRStorage(8, reset=1)
        self._glitchoff = CSRStorage(32)
        self._glitchlen = CSRStorage(8)
        glitchout = Signal()
        glitchmode = Signal()
        glitchtmr = Signal(32)
        self.comb += c2.power.eq(self._pwcon.storage[0] & glitchout)
        self.sync += If(self._pwcon.storage[1],
            self._pwcon.storage[1].eq(0),
            glitchmode.eq(0),
            glitchtmr.eq(self._glitchoff.storage)
        )
        self.sync += If(glitchtmr == 0,
            glitchout.eq(1)
        ).Else(
            glitchtmr.eq(glitchtmr - 1),
            glitchout.eq(~glitchmode),
            If(glitchtmr == 1,
                If(glitchmode == 0,
                    glitchtmr[:8].eq(self._glitchlen.storage),
                    glitchmode.eq(1)
                )
            )
        )


        # when rxbuf is read, reset the buffer full flag
        self.sync += If(self._rxbuf.we, rfull.eq(0))

        fsm = FSM(reset_state="IDLE")
        self.submodules.fsm = fsm

        fsm.act("IDLE",
            c2d.oe.eq(0),
            NextValue(c2ck, 1),
            If(self._cmd.storage == 1,  # data read
                NextValue(self._cmd.storage, 0),
                NextValue(txbuf, 1),  # start(1), data read (00), length (00)
                NextValue(txlen, 5),
                NextValue(rxlen, 8),
                NextValue(readerror, 0),
                NextValue(waitlen, 127),
                NextState("TX")
            ).Elif(self._cmd.storage == 2,  # address write
                NextValue(self._cmd.storage, 0),
                # start (1), address write (11), address
                NextValue(txbuf, (self._txdat.storage << 3) | 7),
                NextValue(txlen, 11),
                NextValue(rxlen, 0),
                NextValue(waitlen, 0),
                NextState("TX")
            ).Elif(self._cmd.storage == 3,  # address read
                NextValue(self._cmd.storage, 0),
                NextValue(waitlen, 0),
                NextValue(txbuf, 5),  # start (1), address read (01)
                NextValue(txlen, 3),
                NextValue(waitlen, 0),  # no wait
                NextValue(rxlen, 8),  # read 8 bits
                NextValue(readerror, 0),
                NextState("TX")
            ).Elif(self._cmd.storage == 4,  # data write
                NextValue(self._cmd.storage, 0),
                NextValue(waitlen, 0),
                # start (1), data write (10), length (00), data
                NextValue(txbuf, (self._txdat.storage << 5) | 3),
                NextValue(txlen, 13),
                NextValue(waitlen, 127),  # wait at the end
                NextValue(rxlen, 0),  # no read
                NextState("TX")
            ).Elif(self._cmd.storage == 5,  # reset
                NextValue(self._cmd.storage, 0),
                NextValue(reset_count, 960),  # 20us at 48MHz
                NextValue(c2ck, 0),
                NextState("RESET")
            )
        )

        fsm.act("RESET",  # 20us reset line low
            NextValue(c2ck, 0),
            If(reset_count == 0,
                NextValue(reset_count, 96),  # 2us at 48MHz
                NextState("RESET2")
            ).Else(
                NextValue(reset_count, reset_count - 1),
            )
        )

        fsm.act("RESET2",  # 2us reset line high
            NextValue(c2ck, 1),
            If(reset_count == 0,
                NextState("IDLE")
            ).Else(
                NextValue(reset_count, reset_count - 1),
            )
        )

        fsm.act("TX",
            # clk initially 1 here
            c2d.oe.eq(1),
            If(txlen == 0,
                If(waitlen != 0,
                    NextState("WAIT"),
                ).Elif(rxlen != 0,
                    NextState("RX"),
                ).Else(
                    NextState("STOP")
                ),
                NextValue(c2ck, 0)
            ).Else(
                If(c2ck == 1,  # clock is high, about to drop the next bit
                    NextValue(c2d.o, txbuf[0]),
                    NextValue(txbuf, txbuf[1:])
                ).Else(
                    # clock is low, about to raise it and potentially advance to the next state
                    NextValue(txlen, txlen-1)
                ),
                NextValue(c2ck, ~c2ck)
            )
        )

        fsm.act("WAIT",
            # must enter state with c2ck already at 0
            c2d.oe.eq(0),
            If((c2ck == 1) & (c2d.i == 1),
                If(rxlen != 0,
                    NextState("RX")
                ).Else(
                    NextState("STOP")
                ),
               NextValue(c2ck, 0)
            ).Else(
                If(waitlen == 0,
                    NextValue(readerror, 1),
                    NextState("IDLE")
                ).Else(
                    NextValue(waitlen, waitlen - 1),
                    NextValue(c2ck, ~c2ck)
                )
            )
        )

        fsm.act("RX",
            # must enter state with c2ck already at 0
            c2d.oe.eq(0),
            If(c2ck == 1,  # clock is high, shift in bit as it falls
                NextValue(rxbuf, Cat(rxbuf[1:], c2d.i)),
                If(rxlen == 1,
                    NextValue(rfull, 1),
                    NextState("STOP")
                ),
                NextValue(c2ck, 0),
                NextValue(rxlen, rxlen - 1),
            ).Else(
                NextValue(c2ck, 1)
            )
        )

        fsm.act("STOP",
            # must enter state with c2ck already at 0
            c2d.oe.eq(0),
            If(c2ck == 1,  # stop done
                NextState("IDLE")
            ).Else(
                NextValue(c2ck, 1)
            )
        )

        # status register byte:
        # |  7  |  6   | 5 | 4 |  3   |  2 |  1 |  0   |
        # | ERR | RRDY | . | . | WAIT | RX | TX | IDLE |
        self.comb += self._stat.status.eq(
            fsm.ongoing("IDLE") | 
            (fsm.ongoing("TX") << 1) |
            (fsm.ongoing("RX") << 2) |
            (fsm.ongoing("WAIT") << 3) | 
            (rfull << 6) |
            (readerror << 7))
        
        # for debugging, expose internals
        self._txlen = CSRStatus(5)
        self._txbuf = CSRStatus(12)
        self._rxlen = CSRStatus(4)
        self._waitlen = CSRStatus(7)

        self.comb += self._txlen.status.eq(txlen)
        self.comb += self._txbuf.status.eq(txbuf)
        self.comb += self._rxlen.status.eq(rxlen)
        self.comb += self._waitlen.status.eq(waitlen)
