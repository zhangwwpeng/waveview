"""Two unrelated clocks: a bit crossing through a two-flop synchroniser."""

from waveview import Diagram

d = Diagram(title="CDC 两级同步")
clka = d.clock("clka", freq="100MHz")
clkb = d.clock("clkb", freq="143MHz", phase="2ns")

with d.group("clka 域"):
    din = d.wire("din", init=0)
with d.group("clkb 域"):
    s1 = d.wire("s1", init=0)
    s2 = d.wire("s2", init=0)

e0 = d.posedge(clka, 2)(din=1)
e1 = d.posedge(clkb)(s1="x")
e2 = d.posedge(clkb)(s1=1, s2="x")
e3 = d.posedge(clkb)(s2=1)

d.region(s1, e1, e2, "亚稳态")
d.arrow(e0.din, e1.s1, "采样")
d.measure(e0, e3, "同步延迟")

if __name__ == "__main__":
    print(d.describe())
    d.save("out/cdc.svg")
    d.save("out/cdc.png")
