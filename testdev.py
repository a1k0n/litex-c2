from migen import Module, TSTriple, Signal, FSM, If, NextState, NextValue
from litex.soc.interconnect.csr import AutoCSR, CSRStorage, CSRStatus


class TestDevice(Module, AutoCSR):
    def __init__(self, c2):
        c2ck = c2.c2ck

        txlen = Signal(5)
        txbuf = Signal(12)
        rxlen = Signal(4)
        waitlen = Signal(7)
        error = Signal()

        self._cmd = CSRStorage(8)
        self._stat = CSRStatus(8)
        self._rdbuf = CSRStatus(8)

        c2d = TSTriple()
        self.specials += c2d.get_tristate(c2.c2d)

        fsm = FSM(reset_state="IDLE")
        self.submodules.fsm = fsm

        fsm.act("IDLE",
                c2d.oe.eq(0),
                NextValue(c2ck, 1),
                If(self._cmd.storage == 1,
                    NextValue(self._cmd.storage, 0),
                    # write 00 (data read) 00 (length)
                    NextValue(txbuf, 0),
                    NextValue(txlen, 4),
                    NextValue(rxlen, 8),
                    NextValue(error, 0),
                    NextValue(waitlen, 40),
                    NextState("TX")
                   )
                )

        fsm.act("TX",
            c2d.oe.eq(1),
            If(txlen == 0,
                If(waitlen != 0,
                    NextState("WAITRX")
                ).Elif(rxlen != 0,
                    NextState("RX")
                ).Else(
                    NextState("IDLE")
                )
            ).Else(
                If(c2ck == 1,  # clock is high, about to drop the next bit
                    NextValue(c2d.o, txbuf & 1)
                ).Else(
                    # clock is low, about to raise it and potentially advance to the next state
                    NextValue(txlen, txlen-1)
                ),
                NextValue(c2ck, ~c2ck)
            )
        )

        fsm.act("WAITRX",
            c2d.oe.eq(0),
            If((c2ck == 1) & (c2d.i == 1),
                NextState("RX"),
                NextValue(c2ck, ~c2ck)
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
            c2d.oe.eq(0),
            If(rxlen == 0,
                NextState("IDLE")
            ).Else(
                If(c2ck == 1, # clock is high, shift in bit
                    self._rdbuf.status.eq(
                        (c2d.i << 7) | (self._rdbuf.status >> 1))
                ),
                NextValue(c2ck, ~c2ck)
            )
        )


        # write 1 to state to initiate read
        self.comb += self._stat.status.eq(
            fsm.ongoing("IDLE") | 
            (fsm.ongoing("TX") << 1) |
            (fsm.ongoing("RX") << 2) |
            (fsm.ongoing("WAITRX") << 3) | 
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

        # t.i, t.o, t.oe
