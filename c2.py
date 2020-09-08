from migen import Module, TSTriple, Array, Signal, Cat, FSM, If, NextState, NextValue
from litex.soc.interconnect.csr import AutoCSR, CSRStorage, CSRStatus


class C2Interface(Module, AutoCSR):
    def __init__(self, c2):

        txwidth = 12
        txlen = Signal(max=txwidth+1)
        txbuf = Signal(txwidth)
        rxbuf = Signal(8)
        rxlen = Signal(4)

        waitlen = Signal(7)
        rfull = Signal()
        error = Signal()

        self._cmd = CSRStorage(8)
        self._stat = CSRStatus(8)
        self._rxbuf = CSRStatus(8)
        self.comb += self._rxbuf.status.eq(rxbuf)
        self._addr = CSRStorage(8)

        c2d = TSTriple()
        c2ck = Signal(reset=1)
        self.comb += c2.c2ck.eq(c2ck)
        self.specials += c2d.get_tristate(c2.c2d)

        # when rxbuf is read, reset the buffer full flag
        self.sync += If(self._rxbuf.we, rfull.eq(0))

        fsm = FSM(reset_state="IDLE")
        self.submodules.fsm = fsm

        fsm.act("IDLE",
            c2d.oe.eq(0),
            NextValue(c2ck, 1),
            If(self._cmd.storage == 1,  # data read
                NextValue(self._cmd.storage, 0),
                # write 00 (data read) 00 (length)
                NextValue(txbuf, 0),
                NextValue(txlen, 5),
                NextValue(rxlen, 8),
                NextValue(error, 0),
                NextValue(waitlen, 40),
                NextState("TX")
            ),
            If(self._cmd.storage == 2,  # address write
                NextValue(self._cmd.storage, 0),
                NextValue(txbuf, (self._addr.storage << 3) | 7),
                NextValue(txlen, 11),
                NextValue(rxlen, 0),
                NextValue(error, 0),
                NextValue(waitlen, 0),
                NextState("TX")
            ),
        )

        fsm.act("TX",
            # clk initially 1 here
            c2d.oe.eq(1),
            If(txlen == 0,
                If(waitlen != 0,
                    NextState("WAITRX"),
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

        fsm.act("WAITRX",
            # must enter state with c2ck already at 0
            c2d.oe.eq(0),
            If((c2ck == 1) & (c2d.i == 1),
                NextState("RX"),
                NextValue(c2ck, 0)
            ).Else(
                If(waitlen == 0,
                    NextValue(error, 1),
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
        # |  7  |  6   | 5 | 4 |   3    |  2 |  1 |  0   |
        # | ERR | RRDY | . | . | WAITRX | RX | TX | IDLE |
        self.comb += self._stat.status.eq(
            fsm.ongoing("IDLE") | 
            (fsm.ongoing("TX") << 1) |
            (fsm.ongoing("RX") << 2) |
            (fsm.ongoing("WAITRX") << 3) | 
            (rfull << 6) |
            (error << 7))
        
        # for debugging, expose internals
        self._txlen = CSRStatus(5)
        self._txbuf = CSRStatus(12)
        self._rxlen = CSRStatus(4)
        self._waitlen = CSRStatus(7)

        self.comb += self._txlen.status.eq(txlen)
        self.comb += self._txbuf.status.eq(txbuf)
        self.comb += self._rxlen.status.eq(rxlen)
        self.comb += self._waitlen.status.eq(waitlen)
