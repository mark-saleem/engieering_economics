#!/usr/bin/env python3
"""
GENG-3130 Engineering Economics Calculator  (pygame)

Run:   pip install pygame
       python econ_calc.py

Menu page -> pick a question type -> type values -> answers update live.

Keys (calculator screen)
  Tab / Down / Enter   next field        Shift+Tab / Up   previous field
  Left / Right / Space change a dropdown ("Solve for", "Direction", ...)
  Mouse wheel / PgUp / PgDn  scroll results and tables
  Drag the scrollbar on the right edge (menu and results panel)
  Ctrl+K               continuous compounding ON/OFF (also a button in the header)
  Ctrl+R               reset this screen to its example values
  Esc                  back to the menu

Typing rules
  * Rates are typed as PERCENT:  8  means 8%.   You can type expressions:  6/12  = 0.5%,  1.2*3
  * Money / numbers: 800000 or 800,000 or 8e5 all work. Expressions work too: 0.06/12
  * Lists (cash flows, deposits) are comma separated:  100, 80, 120, 150
  * The first key you type in a field replaces its old value.
"""
import ast
import math
import operator
import re
import sys

import pygame

# =====================================================================
#  FINANCE FORMULAS  (all take the interest rate as a fraction)
# =====================================================================


TBL = [False]    # True = round every factor to 5 significant digits, like the printed tables
MISS = [False]   # set when a factor was asked for a rate / N that the printed tables don't contain
CONT = [False]   # True = continuous compounding mode (typed rate is nominal r, used i = e^r - 1)


def table_ok(i, n):
    """Would the printed interest tables contain this rate and N?"""
    p = i * 100
    return 0 < p <= 50 and abs(p * 4 - round(p * 4)) < 1e-7 and abs(n - round(n)) < 1e-9 and 1 <= n <= 100


def _t(v, i, n):
    if TBL[0]:
        if table_ok(i, n):
            return float("%.5g" % v)
        MISS[0] = True
    return v


def FP(i, n): return _t((1 + i) ** n, i, n)
def PF(i, n): return _t((1 + i) ** (-n), i, n)
def AF(i, n): return _t(1.0 / n if i == 0 else i / ((1 + i) ** n - 1), i, n)
def FA(i, n): return _t(float(n) if i == 0 else ((1 + i) ** n - 1) / i, i, n)
def AP(i, n): return _t(1.0 / n if i == 0 else i * (1 + i) ** n / ((1 + i) ** n - 1), i, n)
def PA(i, n): return _t(float(n) if i == 0 else ((1 + i) ** n - 1) / (i * (1 + i) ** n), i, n)
def AG(i, n): return _t((n - 1) / 2.0 if i == 0 else 1.0 / i - n / ((1 + i) ** n - 1), i, n)
def I0(i, g): return (1 + i) / (1 + g) - 1


def PAg(i, g, n):
    """(P/A, g, i, N): geometric gradient to present worth."""
    z = I0(i, g)
    if abs(z) < 1e-12:
        return n / (1 + g)
    return PA(z, n) / (1 + g)


def irr(flows, lo=-0.95, hi=5.0):
    """Rate where NPV = 0 (first sign change found by scanning), or None."""
    def npv(r):
        return sum(c / (1 + r) ** t for t, c in enumerate(flows))
    steps = 6000
    prev_r, prev_v = lo, npv(lo)
    for k in range(1, steps + 1):
        r = lo + (hi - lo) * k / steps
        try:
            val = npv(r)
        except ZeroDivisionError:
            continue
        if prev_v == 0:
            return prev_r
        if prev_v * val < 0:
            a, b = prev_r, r
            for _ in range(100):
                m = (a + b) / 2
                if npv(a) * npv(m) <= 0:
                    b = m
                else:
                    a = m
            return (a + b) / 2
        prev_r, prev_v = r, val
    return None


# =====================================================================
#  SAFE NUMBER PARSING
# =====================================================================
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def _ev(n):
    if isinstance(n, ast.Expression):
        return _ev(n.body)
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return float(n.value)
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.left), _ev(n.right))
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.operand))
    raise ValueError("bad expression")


def parse_num(s):
    s = s.strip().replace("$", "").replace("%", "").replace("^", "**").replace(",", "")
    if not s:
        raise ValueError("empty")
    return float(_ev(ast.parse(s, mode="eval")))


def parse_list(s):
    parts = [p for p in re.split(r"[,;\s]+", s.strip()) if p]
    return [parse_num(p) for p in parts]


# =====================================================================
#  OUTPUT HELPERS
# =====================================================================
def money(x): return ("-$%s" % format(abs(x), ",.2f")) if x < 0 else "$" + format(x, ",.2f")
def fac(x): return "%.5f" % x
def pc(x, d=4): return "%.*f%%" % (d, x * 100)
def num(x, d=4): return format(x, ",.%df" % d)


class InputError(Exception):
    """Raised by a calculator to show a plain-English message instead of a generic one."""


class Out:
    def __init__(self, style, label="", value=""):
        self.style, self.label, self.value = style, label, value
        self.tv = None   # same answer using rounded table factors (filled in automatically)


def K(label, value): return Out("key", label, value)
def O(label, value): return Out("norm", label, value)
def N(text): return Out("note", text)
def H(text): return Out("head", text)


class Field:
    """kind: num | pct | list | plist | choice"""

    def __init__(self, key, label, default="", kind="num", opts=None, hint="", wide=False, cont=False):
        self.key, self.label, self.default = key, label, default
        self.kind, self.opts, self.hint = kind, opts or [], hint
        self.wide, self.cont = wide, cont   # wide = full-width box; cont = convert with e^r-1 in continuous mode


class Calc:
    def __init__(self, cat, title, desc, fields, fn, formula="", note="", active=None, kw="", notable=False):
        self.cat, self.title, self.desc = cat, title, desc
        self.fields, self.fn = fields, fn
        self.formula, self.note, self.active, self.kw = formula, note, active, kw
        self.notable = notable   # True = skip the automatic "with table factors" second pass


CALCS = []


def reg(*a, **k):
    CALCS.append(Calc(*a, **k))


# =====================================================================
#  CALCULATORS
# =====================================================================
# ---------- Factors ----------
def c_factors(v):
    i, n, g = v["i"], v["N"], v["g"]
    z = I0(i, g)
    return [
        H("Single payment"),
        O("Compound Amount  (F/P, i, N)", fac(FP(i, n))),
        O("Present Worth  (P/F, i, N)", fac(PF(i, n))),
        H("Annuity"),
        O("Sinking Fund  (A/F, i, N)", fac(AF(i, n))),
        O("Uniform Series Compound Amount  (F/A, i, N)", fac(FA(i, n))),
        O("Capital Recovery  (A/P, i, N)", fac(AP(i, n))),
        O("Series Present Worth  (P/A, i, N)", fac(PA(i, n))),
        H("Arithmetic gradient"),
        O("Gradient to Annuity  (A/G, i, N)", fac(AG(i, n))),
        O("Gradient to Present  (P/G) = (A/G)(P/A)", fac(AG(i, n) * PA(i, n))),
        O("Gradient to Future  (F/G) = (A/G)(F/A)", fac(AG(i, n) * FA(i, n))),
        H("Geometric gradient (uses g)"),
        O("Growth-adjusted rate  i° = (1+i)/(1+g) - 1", pc(z, 5)),
        O("Geometric to Present  (P/A, g, i, N)", fac(PAg(i, g, n))),
        N("Textbook check (i = 8%, N = 10):  F/P = 2.1589, P/A = 6.7101, A/G = 3.8713."),
    ], None


reg("Basics", "Interest Factors", "Every factor for ANY i and N (replaces the tables)",
    [Field("i", "Interest rate per period, i (%)", "8", "pct"),
     Field("N", "Number of periods, N", "10"),
     Field("g", "Growth rate g (%) (geometric only)", "3", "pct")],
    c_factors,
    "(F/P)=(1+i)^N   (P/F)=1/(1+i)^N   (A/F)=i/((1+i)^N-1)   (F/A)=((1+i)^N-1)/i\n"
    "(A/P)=i(1+i)^N/((1+i)^N-1)   (P/A)=((1+i)^N-1)/(i(1+i)^N)   (A/G)=1/i-N/((1+i)^N-1)\n"
    "i°=(1+i)/(1+g)-1   (P/A,g,i,N)=(P/A,i°,N)/(1+g)",
    kw="table factor fp pf af fa ap pa ag")


# ---------- Single payment ----------
def a_single(v):
    return {"F": {"P", "i", "N"}, "P": {"F", "i", "N"}, "i": {"P", "F", "N"}, "N": {"P", "F", "i"}}[v["solve"][0]]


def c_single(v):
    s = v["solve"][0]
    P, F, i, n = v["P"], v["F"], v["i"], v["N"]
    if s == "F":
        r = P * FP(i, n)
        return [K("Future amount  F = P(1+i)^N", money(r)), O("(F/P, i, N)", fac(FP(i, n))),
                O("Interest earned  F - P", money(r - P))], None
    if s == "P":
        r = F * PF(i, n)
        return [K("Present amount  P = F/(1+i)^N", money(r)), O("(P/F, i, N)", fac(PF(i, n)))], None
    if s == "i":
        r = (F / P) ** (1.0 / n) - 1
        return [K("Interest rate per period  i = (F/P)^(1/N) - 1", pc(r))], None
    ex = math.log(F / P) / math.log(1 + i)
    return [K("Number of periods  N = ln(F/P)/ln(1+i)", num(ex)),
            O("Whole periods needed (round up)", "%d" % math.ceil(ex - 1e-12))], None


reg("Basics", "Single Payment: F, P, i or N", "One cash flow now and one later; solve for any one",
    [Field("solve", "Solve for", opts=["F (future)", "P (present)", "i (rate)", "N (periods)"], kind="choice"),
     Field("P", "Present amount, P ($)", "2000"), Field("F", "Future amount, F ($)", "4287.18"),
     Field("i", "Interest rate per period, i (%)", "10", "pct"), Field("N", "Number of periods, N", "8")],
    c_single,
    "F = P(1+i)^N     P = F/(1+i)^N     i = (F/P)^(1/N) - 1     N = ln(F/P) / ln(1+i)",
    "Slide checks: P=2000, 10%, N=8 -> F=4,287.18 | F=100,000 at 8%, N=10 -> P=46,319 | P=10, F=20, N=5 -> i=14.87% | "
    "P=6000, F=12000, 20% -> N=3.80 years.",
    active=a_single, kw="compound discount lump sum")


def c_simple(v):
    P, i, n = v["P"], v["i"], v["N"]
    s, c = P * (1 + i * n), P * (1 + i) ** n
    return [K("Simple interest   F = P(1 + iN)", money(s)), K("Compound interest   F = P(1+i)^N", money(c)),
            O("Difference", money(c - s))], None


reg("Basics", "Simple vs Compound Interest", "Compare the two for the same P, i and N",
    [Field("P", "Principal, P ($)", "100"), Field("i", "Interest rate per period, i (%)", "10", "pct"),
     Field("N", "Number of periods, N", "3")],
    c_simple, "Simple: F = P(1 + iN)      Compound: F = P(1+i)^N",
    "Slide check: $100, 10%, 3 years -> simple $130.00, compound $133.10.")


# ---------- Annuity ----------
def a_annuity(v):
    return {"F": {"A", "i", "N"}, "P": {"A", "i", "N"}, "AF": {"F", "i", "N"}, "AP": {"P", "i", "N"}}[
        {"F given A": "F", "P given A": "P", "A given F": "AF", "A given P": "AP"}[v["solve"]]]


def c_annuity(v):
    s = v["solve"]
    A, F, P, i, n = v["A"], v["F"], v["P"], v["i"], v["N"]
    if s == "F given A":
        return [K("Future worth at end of N   F = A(F/A,i,N)", money(A * FA(i, n))),
                O("Present worth at t=0   P = A(P/A,i,N)", money(A * PA(i, n))),
                O("(F/A, i, N)", fac(FA(i, n))), O("(P/A, i, N)", fac(PA(i, n)))], None
    if s == "P given A":
        return [K("Present worth at t=0   P = A(P/A,i,N)", money(A * PA(i, n))),
                O("Future worth at end of N   F = A(F/A,i,N)", money(A * FA(i, n))),
                O("(P/A, i, N)", fac(PA(i, n)))], None
    if s == "A given F":
        return [K("Payment each period   A = F(A/F,i,N)", money(F * AF(i, n))), O("(A/F, i, N)", fac(AF(i, n)))], None
    return [K("Payment each period   A = P(A/P,i,N)", money(P * AP(i, n))), O("(A/P, i, N)", fac(AP(i, n)))], None


reg("Basics", "Annuity: A, F or P", "Equal payments at the END of each period (first at t=1)",
    [Field("solve", "Solve for", opts=["F given A", "P given A", "A given F", "A given P"], kind="choice"),
     Field("A", "Payment each period, A ($)", "3000"), Field("F", "Future amount, F ($)", "50000"),
     Field("P", "Present amount, P ($)", "250000"),
     Field("i", "Interest rate per period, i (%)", "7", "pct"), Field("N", "Number of payments, N", "10")],
    c_annuity,
    "F = A(F/A,i,N)     P = A(P/A,i,N)     A = F(A/F,i,N)     A = P(A/P,i,N)\n"
    "First payment is at the END of period 1. A payment at t=0 is NOT part of the annuity.",
    "Slide checks: A=3000, 7%, N=10 -> F=41,449 | F=50,000, 10%, N=5 -> A=8,189.87 | P=250,000, 8%, N=6 -> A=54,079 "
    "(slide 54,080 uses rounded table).",
    active=a_annuity, kw="loan payment sinking fund capital recovery")


def c_nonstd(v):
    A, i, s, e, T = v["A"], v["i"], v["s"], v["e"], v["T"]
    n = e - s + 1
    at = A * PA(i, n)
    pw0 = at * (1 + i) ** (-(s - 1))
    return [O("Number of payments  n = e - s + 1", "%g" % n),
            O("Value at t = s-1   A(P/A,i,n)", money(at)),
            K("Present worth at t = 0", money(pw0)),
            K("Equivalent value at time T", money(pw0 * (1 + i) ** T)),
            O("Future worth at t = e (last payment)   A(F/A,i,n)", money(A * FA(i, n)))], None


reg("Basics", "Annuity Not Starting at t=1", "Payments from period s to period e; value at any time",
    [Field("A", "Payment each period, A ($)", "1500"), Field("i", "Interest rate per period, i (%)", "5", "pct"),
     Field("s", "First payment at period, s", "7"), Field("e", "Last payment at period, e", "10"),
     Field("T", "Time T for equivalent value", "0", hint="T = 0 gives present worth")],
    c_nonstd,
    "P(s-1) = A(P/A,i,n)  then  P0 = P(s-1) (P/F,i,s-1)   ->  move to any time T with (F/P,i,T)",
    "Slide check: A=1500 for t=7..10 at 5% -> PW = 3,969.14 (add 1000 for t=1..6 -> total 9,044.84).",
    kw="delayed deferred")


def c_uneven(v):
    i, T, cf = v["i"], v["T"], v["cf"]
    if not cf:
        raise ValueError
    n = len(cf) - 1
    valT = sum(c * (1 + i) ** (T - t) for t, c in enumerate(cf))
    pw = sum(c * (1 + i) ** (-t) for t, c in enumerate(cf))
    r = irr(cf)
    outs = [K("Equivalent value at time T", money(valT)), K("Present worth at t = 0", money(pw)),
            O("Future worth at t = %d (last flow)" % n, money(pw * (1 + i) ** n)),
            O("Equivalent uniform amount over t=1..%d" % n, money(pw * AP(i, n)) if n > 0 else "n/a"),
            O("IRR of this stream (PW = 0)", pc(r) if r is not None else "none (needs + and - flows)")]
    rows = [[str(t), "%.2f" % c, "%.5f" % ((1 + i) ** (T - t)), "%.2f" % (c * (1 + i) ** (T - t))] for t, c in enumerate(cf)]
    return outs, (["t", "Cash flow", "(1+i)^(T-t)", "Value at T"], rows)


reg("Basics", "Uneven Cash Flows (any time)", "List the flow at t=0,1,2...; get value at any time T",
    [Field("i", "Interest rate per period, i (%)", "10", "pct"),
     Field("T", "Time T for equivalent value", "3"),
     Field("cf", "Cash flows at t=0,1,2,... (list)", "100, 80, 120, 150, 200, 100", "list",
           hint="Put 0 for periods with no flow. + = receipt, - = cost")],
    c_uneven, "V(T) = sum of CF(t) x (1+i)^(T-t)       (compounding forward if t < T, discounting if t > T)",
    "Slide check (Practice Problem 1): the default flows at 10%, T=3 -> $776.36. "
    "Module 4 'extreme MARR' example: -100000, 50000, 50000 at 5%, T=0 -> -7,029.", kw="equivalent irr")


# ---------- Gradients ----------
def c_arith(v):
    i, n, A1, G, d, k, T, C0 = v["i"], v["N"], v["A1"], v["G"], v["dir"], v["k"], v["T"], v["C0"]
    sgn = 1 if d == "Increasing" else -1
    ag = AG(i, n)
    At = A1 + sgn * G * ag
    at_k = At * PA(i, n)
    pw = at_k * (1 + i) ** (-k)
    vT = pw * (1 + i) ** T
    return [O("(A/G, i, N)", fac(ag)), O("G x (A/G, i, N)", money(G * ag)),
            K("Equivalent uniform series  A_total", money(At)),
            O("Value at t = k  (one period before 1st flow)", money(at_k)),
            K("Present worth at t = 0  (series only)", money(pw)),
            O("Future worth at t = k+N  (last flow)", money(At * FA(i, n))),
            O("Value of series at time T", money(vT)),
            K("Value at T including the t=0 flow", money(vT + C0 * (1 + i) ** T)),
            O("Present worth at t=0 including t=0 flow", money(pw + C0))], None


reg("Gradients", "Arithmetic Gradient  A' +/- G", "A_total, PW, FW, value at time T (2023 midterm style)",
    [Field("i", "Interest rate per period, i (%)", "3", "pct"),
     Field("N", "Number of flows in the series, N", "16", hint="Count every flow in the series, including the first"),
     Field("A1", "First flow of the series, A' ($)", "10000"), Field("G", "Gradient, G ($) (positive)", "500"),
     Field("dir", "Direction", opts=["Decreasing", "Increasing"], kind="choice"),
     Field("k", "Delay k (first flow at t = k+1)", "1", hint="Series from t=1 -> k=0; from t=2 -> k=1; from t=3 -> k=2"),
     Field("T", "Time T for equivalent value", "20", hint="T = 0 gives present worth"),
     Field("C0", "Extra flow at t=0 ($) (cost negative)", "-8000")],
    c_arith,
    "A_total = A' + G(A/G,i,N)  (increasing)   or   A' - G(A/G,i,N)  (decreasing)\n"
    "P(t=k) = A_total (P/A,i,N)     P0 = P(t=k)(P/F,i,k)     F = A_total (F/A,i,N)\n"
    "Pure textbook gradient (0, G, 2G...): set A' = 0, Increasing, k = 0.",
    "Slide checks: default = 2023 midterm -> A_total 6,562.9, F20 = 130,102.7 (slide rounds factors; exact 130,105). "
    "Truck: 12%, N=5, A'=1000, G=250, Incr., k=0, T=0, C0=0 -> PW 5,204.07. "
    "Declining: 10%, N=5, A'=1200, G=200 -> A_total 837.98.",
    kw="linear gradient ag")


def c_arith_solve(v):
    i, n, G, d, k, PW = v["i"], v["N"], v["G"], v["dir"], v["k"], v["PW"]
    sgn = 1 if d == "Increasing" else -1
    ag = AG(i, n)
    At = PW / (PA(i, n) * (1 + i) ** (-k))
    return [O("(A/G, i, N)", fac(ag)), O("A_total needed", money(At)),
            K("First flow  A' needed", money(At - sgn * G * ag))], None


reg("Gradients", "Arithmetic: Solve for A'", "Given the present worth, find the first cash flow (Q9 2024)",
    [Field("i", "Interest rate per period, i (%)", "6", "pct"), Field("N", "Number of flows, N", "12"),
     Field("G", "Gradient, G ($) (positive)", "5000"),
     Field("dir", "Direction", opts=["Decreasing", "Increasing"], kind="choice"),
     Field("k", "Delay k (first flow at t = k+1)", "2"),
     Field("PW", "Target present worth at t=0 ($)", "715890.3")],
    c_arith_solve, "A_total = PW / [(P/A,i,N)(P/F,i,k)]      A' = A_total -/+ G(A/G,i,N)",
    "Slide check (Q9 2024): defaults -> A' = 120,000.")


def c_geo(v):
    i, g, n, A, k, T = v["i"], v["g"], v["N"], v["A"], v["k"], v["T"]
    f = PAg(i, g, n)
    at_k = A * f
    pw = at_k * (1 + i) ** (-k)
    return [O("Growth-adjusted rate  i° = (1+i)/(1+g) - 1", pc(i0 := I0(i, g), 5)),
            O("(P/A, g, i, N)", "%.5f" % f),
            O("Value at t = k  (one period before 1st flow)", money(at_k)),
            K("Present worth at t = 0", money(pw)),
            K("Future worth at t = k+N (last flow)", money(at_k * (1 + i) ** n)),
            O("Equivalent value at time T", money(pw * (1 + i) ** T)),
            O("Last cash flow  A(1+g)^(N-1)", money(A * (1 + g) ** (n - 1)))], None


reg("Gradients", "Geometric Gradient  A, A(1+g)...", "Growing/shrinking series: PW, FW, value at T",
    [Field("i", "Interest rate per period, i (%)", "1.5", "pct"), Field("g", "Growth rate per period, g (%)", "0.25", "pct"),
     Field("N", "Number of flows, N", "24"), Field("A", "First cash flow, A ($)", "8000"),
     Field("k", "Delay k (first flow at t = k+1)", "0"), Field("T", "Time T for equivalent value", "24")],
    c_geo,
    "i° = (1+i)/(1+g) - 1      (P/A,g,i,N) = (P/A,i°,N)/(1+g)      P = A (P/A,g,i,N)\n"
    "If g = i then i° = 0 and P = N A/(1+g).   g > i gives a NEGATIVE i° - use the formula, not a table.",
    "Slide checks: Tru-Test (defaults) -> PW 164,580 (slide rounds i° to 1.25%; exact 164,643). "
    "A=54,600, g=7%, i=12%, N=5 -> 222,937. A=380, g=8%, i=5%, N=4 -> 1,511. "
    "A=5500, g=3%, i=12%, N=30, k=0, T=30 -> FW 1,682,235. "
    "Reginald: A=10,000, g=10%, i=20%, N=10, k=0, T=10 -> 359,801 (g=20%, i=10% -> 359,790).",
    kw="growth inflation reginald")


def a_geosolve(v):
    return {"F": {"F"}, "P": {"P"}}[v["mode"][0]] | {"i", "g", "N", "k"}


def c_geosolve(v):
    i, g, n, k = v["i"], v["g"], v["N"], v["k"]
    f = PAg(i, g, n)
    if v["mode"][0] == "F":
        A = v["F"] / (f * (1 + i) ** n)
    else:
        A = v["P"] * (1 + i) ** k / f
    return [O("(P/A, g, i, N)", "%.5f" % f), K("First cash flow  A needed", money(A))], None


reg("Gradients", "Geometric: Solve for First Deposit", "Given a future (or present) target, find A",
    [Field("mode", "Target is", opts=["F (future worth at end)", "P (present worth at t=0)"], kind="choice"),
     Field("F", "Target future worth, F ($)", "1000000"), Field("P", "Target present worth, P ($)", "164580"),
     Field("i", "Interest rate per period, i (%)", "8", "pct"), Field("g", "Growth rate per period, g (%)", "6", "pct"),
     Field("N", "Number of deposits, N", "20"), Field("k", "Delay k (first flow at t = k+1)", "0")],
    c_geosolve, "F = A (P/A,g,i,N)(F/P,i,N)  ->  A = F / [(P/A,g,i,N)(F/P,i,N)]        P0 = A (P/A,g,i,N)(P/F,i,k)",
    "Slide check (retirement): F=1,000,000, i=8%, g=6%, N=20 -> A = 13,757.", active=a_geosolve, kw="retirement")


# ---------- Capital recovery etc ----------
def c_caprec(v):
    P, S, n, i, B = v["P"], v["S"], v["N"], v["i"], v["B"]
    A = (P - S) * AP(i, n) + S * i
    outs = [K("Capital recovery cost = required net annual savings", money(A)),
            O("Check  P(A/P) - S(A/F)", money(P * AP(i, n) - S * AF(i, n))),
            O("(A/P, i, N)", fac(AP(i, n)))]
    if B:
        outs += [O("Actual net annual benefit", money(B)), O("Benefit - capital recovery cost", money(B - A)),
                 K("Justified?", "YES" if B >= A else "NO")]
    return outs, None


reg("Capital & Rates", "Capital Recovery Cost", "Required annual savings to justify a purchase (ABC-Plastic)",
    [Field("P", "Purchase price, P ($)", "800000"), Field("S", "Salvage value, S ($)", "150000"),
     Field("N", "Useful life, N", "8"), Field("i", "Interest rate per period, i (%)", "9", "pct"),
     Field("B", "Actual net annual benefit ($) (0 = skip)", "0")],
    c_caprec, "A = (P - S)(A/P,i,N) + S i        (same as P(A/P) - S(A/F))",
    "Slide checks: 71,000 / 8,000 / 5 yrs / 15% -> 19,994 (table) | 500,000 / 100,000 / 10 yrs / 6% -> 60,348.",
    kw="abc plastic injection molding savings equivalent annual cost")


def c_maxprice(v):
    A, S, n, i = v["A"], v["S"], v["N"], v["i"]
    return [O("PW of the savings   A(P/A,i,N)", money(A * PA(i, n))), O("PW of the salvage   S(P/F,i,N)", money(S * PF(i, n))),
            K("Maximum price you can afford", money(A * PA(i, n) + S * PF(i, n)))], None


reg("Capital & Rates", "Maximum Price to Pay", "Annual savings + salvage -> most you should pay now",
    [Field("A", "Annual savings, A ($)", "80000"), Field("S", "Salvage value, S ($)", "100000"),
     Field("N", "Life, N", "10"), Field("i", "Interest rate per period, i (%)", "8", "pct")],
    c_maxprice, "P = A(P/A,i,N) + S(P/F,i,N)",
    "Slide checks: 80,000/yr, 8%, 10 yrs -> 536,808 with no salvage; with 100,000 salvage -> 583,127 (slide uses rounded table factors; exact 583,126).", kw="wave soldering")


def c_capval(v):
    A, i, F0 = v["A"], v["i"], v["F0"]
    return [K("Capitalized value  A / i", money(A / i)), K("Total PW = first cost + A / i", money(F0 + A / i))], None


reg("Capital & Rates", "Capitalized Value (N -> infinity)", "Perpetuities: P = A / i",
    [Field("A", "Recurring amount per period, A ($)", "15000"), Field("i", "Interest rate per period, i (%)", "10", "pct"),
     Field("F0", "First cost at t = 0 ($) (0 if none)", "1500000")],
    c_capval, "P = A / i",
    "Slide checks: Tucson reservoir (1.5M + 15,000 at 10%) -> 1,650,000 | bypass (500,000 - 125,000 at 10%, no first cost) "
    "-> 3,750,000 | lottery: 1000/week at 0.14% -> 714,285.7.", kw="perpetuity infinite")


def c_eff(v):
    r, m = v["r"], v["m"]
    cols = [0.06, 0.10, 0.12, 0.20, 0.24, 0.25]
    rows = []
    for mm in (1, 2, 4, 12, 52, 365):
        rows.append([str(mm)] + ["%.4f%%" % (((1 + c / mm) ** mm - 1) * 100) for c in cols])
    rows.append(["cont."] + ["%.4f%%" % ((math.exp(c) - 1) * 100) for c in cols])
    return [O("Rate per compounding period  i_s = r/m", pc(r / m, 6)),
            K("Effective annual rate  i_e = (1+r/m)^m - 1", pc((1 + r / m) ** m - 1)),
            O("Continuous compounding  i_e = e^r - 1", pc(math.exp(r) - 1)),
            N("Table: effective annual rate for nominal rate (columns) and m per year (rows).")], \
        (["m \\ r"] + ["%d%%" % round(c * 100) for c in cols], rows)


reg("Capital & Rates", "Nominal -> Effective Rate", "i_e from r and m, plus continuous compounding",
    [Field("r", "Nominal annual rate, r (%)", "24", "pct"), Field("m", "Compounding periods per year, m", "365")],
    c_eff, "i_e = (1 + r/m)^m - 1          continuous: i_e = e^r - 1",
    "Slide checks: 24% daily -> 27.11% | 12% continuous -> 12.75% | 25% semi-annual 26.56%, quarterly 27.44%, continuous 28.40%.",
    kw="ear effective apr")


def c_eff2nom(v):
    ie, m = v["ie"], v["m"]
    s = (1 + ie) ** (1 / m) - 1
    return [O("Rate per compounding period", pc(s, 6)), K("Nominal annual rate  r = m[(1+i_e)^(1/m) - 1]", pc(m * s))], None


reg("Capital & Rates", "Effective -> Nominal Rate", "Find r when you know i_e and m",
    [Field("ie", "Effective annual rate, i_e (%)", "8.2432", "pct"), Field("m", "Compounding periods per year, m", "4")],
    c_eff2nom, "r = m[(1 + i_e)^(1/m) - 1]", "Check: 8% nominal compounded quarterly = 8.2432% effective (slide rounds to 8.24%), so 8.2432 -> 8.0000%.")


def c_payper(v):
    r, m, k = v["r"], v["m"], v["k"]
    return [K("Effective rate per payment period  (1+r/m)^(m/k) - 1", pc((1 + r / m) ** (m / k) - 1, 6)),
            N("Use this as 'i' in every factor when cash flows come once per payment period "
              "(e.g. quarterly deposits with annual withdrawals).")], None


reg("Capital & Rates", "Rate per Payment Period", "Compounding differs from payment frequency",
    [Field("r", "Nominal annual rate, r (%)", "6", "pct"), Field("m", "Compounding periods per year, m", "4"),
     Field("k", "Payments per year, k", "1")],
    c_payper, "i = (1 + r/m)^(m/k) - 1", "Example: 6% compounded quarterly with annual payments -> 6.1364%.")


def c_double(v):
    i = v["i"]
    return [O("Simple interest   N = 1/i", num(1 / i)), K("Compound annually   N = ln2/ln(1+i)", num(math.log(2) / math.log(1 + i))),
            O("Continuous (nominal r = i)   N = ln2/ln(e^r)", num(math.log(2) / i)),
            O("Rule of 72   N = 72/(i in %)", num(72 / (i * 100)))], None


reg("Capital & Rates", "Doubling Time", "How long to double an amount (simple / compound / continuous)",
    [Field("i", "Interest rate per year, i (%)", "11", "pct")],
    c_double, "Simple N=1/i    Compound N=ln2/ln(1+i)    Continuous N=ln2/r    Rule of 72: N=72/i%",
    "Slide check (11%): simple 9.09 yrs, compound 6.64 (doubles in year 7), continuous 6.30 yrs.")


# ---------- Bonds ----------
def bond_price(face, c, i, n, now):
    if now:
        return c + c * PA(i, n - 1) + face * PF(i, n - 1)
    return c * PA(i, n) + face * PF(i, n)


BOND_FIELDS = [Field("face", "Face (par) value ($)", "10000"), Field("cr", "Stated coupon rate, annual (%)", "4.8", "pct"),
               Field("m", "Coupon payments per year", "4"), Field("yrs", "Years to maturity", "30"),
               Field("mr", "Market rate, nominal annual (%)", "6", "pct", hint="Compounded at the coupon frequency"),
               Field("first", "First coupon paid", opts=["End of period 1", "Now (t=0)"], kind="choice")]


def c_bond(v):
    face, cr, m, yrs, mr = v["face"], v["cr"], v["m"], v["yrs"], v["mr"]
    c, i, n = face * cr / m, mr / m, round(yrs * m)
    p = bond_price(face, c, i, n, v["first"].startswith("Now"))
    return [O("Coupon per period  C = Face x rate / m", money(c)), O("Market rate per period, i", pc(i, 4)),
            O("Number of coupon payments, n", "%d" % n), K("Bond present worth (price today)", money(p)),
            O("Premium or discount?", "Premium (above par)" if p > face + 1e-9 else ("Discount (below par)" if p < face - 1e-9 else "At par"))], None


reg("Loans & Bonds", "Bond Price", "PW of coupons + PW of face value",
    BOND_FIELDS, c_bond,
    "P = C(P/A,i,n) + Face(P/F,i,n)      C = Face x coupon rate / payments per year\n"
    "If the first coupon is paid NOW: P = C + C(P/A,i,n-1) + Face(P/F,i,n-1)",
    "Slide checks: T-bond (defaults) -> 8,335.04 | face 5000, coupon 6%/yr quarterly, 9 yrs, market 8%, first coupon NOW -> 4,450.08 "
    "(same bond, first coupon end of qtr 1 -> 4,362.76). The slides use rounded table factors; exact math gives 4,450.03 and 4,362.78.", kw="coupon face par premium discount")


def c_yield(v):
    face, cr, m, yrs, price = v["face"], v["cr"], v["m"], v["yrs"], v["price"]
    c, n, now = face * cr / m, round(yrs * m), v["first"].startswith("Now")
    lo, hi = 1e-9, 5.0
    if bond_price(face, c, lo, n, now) < price:
        raise ValueError
    for _ in range(200):
        mid = (lo + hi) / 2
        if bond_price(face, c, mid, n, now) > price:
            lo = mid
        else:
            hi = mid
    i = (lo + hi) / 2
    return [O("Yield per period", pc(i, 5)), K("Nominal annual yield", pc(i * m, 4)),
            O("Effective annual yield", pc((1 + i) ** m - 1, 4))], None


reg("Loans & Bonds", "Bond Yield from Price", "Market rate implied by the price you pay",
    BOND_FIELDS[:4] + [Field("price", "Price you would pay ($)", "8000"), BOND_FIELDS[5]],
    c_yield, "Find i such that  price = C(P/A,i,n) + Face(P/F,i,n)   (solved numerically)",
    "Check: price from the Bond Price screen (8,335.04 for the defaults) returns 6.00% nominal.")


# ---------- Loans ----------
def c_loan(v):
    P, i, n = v["P"], v["i"], int(round(v["N"]))
    A = P * AP(i, n)
    bal, rows = P, []
    for k in range(1, min(n, 600) + 1):
        it = bal * i
        end = bal + it - A
        rows.append([str(k), "%.2f" % bal, "%.2f" % it, "%.2f" % A, "%.2f" % max(end, 0) if abs(end) > 1e-6 else "0.00"])
        bal = end
    return [K("Payment per period  A = P(A/P,i,N)", money(A)), O("(A/P, i, N)", fac(AP(i, n))),
            O("Total paid", money(A * n)), O("Total interest", money(A * n - P))], \
        (["#", "Begin balance", "Interest", "Payment", "End balance"], rows)


reg("Loans & Bonds", "Loan Payment + Schedule", "Payment per period and full amortization table",
    [Field("P", "Loan amount, P ($)", "50000"),
     Field("i", "Interest rate PER PAYMENT PERIOD (%)", "6/12", "pct", hint="6% nominal monthly -> type 6/12 (= 0.5% per month)"),
     Field("N", "Number of payments, N", "60")],
    c_loan, "A = P(A/P,i,N)      interest_k = balance_(k-1) x i",
    "Slide check: Mustang $50,000, 6%/12, 60 months -> 966.50 on the slide (table factor); exact 966.64. At 5.9%: i = 0.059/12 -> ~964.3.",
    kw="mortgage car amortization")


def c_payoff(v):
    P, i, A = v["P"], v["i"], v["A"]
    x = 1 - P * i / A
    if x <= 0:
        raise ValueError
    n = -math.log(x) / math.log(1 + i)
    full = int(math.floor(n))
    last = (P - A * PA(i, full)) * (1 + i) ** (full + 1)
    return [K("Exact number of payments, N", num(n)), O("Full-size payments", "%d" % full),
            K("Final smaller payment (at period %d)" % (full + 1), money(last)),
            N("Solve (P/A,i,N) = P/A then round N up; the last payment is the leftover, paid one period after the last full one.")], None


reg("Loans & Bonds", "Loan Payoff Time", "How many payments at a fixed amount, and the last one",
    [Field("P", "Loan amount, P ($)", "300000"), Field("i", "Interest rate per period, i (%)", "5", "pct"),
     Field("A", "Payment per period, A ($)", "50000")],
    c_payoff, "(P/A,i,N) = P/A  ->  N = -ln(1 - P i/A)/ln(1+i)      Last = [P - A(P/A,i,N_full)](1+i)^(N_full+1)",
    "Slide check (2023 Q6): 300,000 at 5%, 50,000/yr -> 8 payments, last payment 15,779.21 on the slide (rounded table factors); exact math gives 15,781.19.")


# ---------- Depreciation ----------
def a_dep(v):
    base = {"P", "S", "N", "n"}
    return base | ({"d"} if v["dsrc"] == "Given d" else set())


def c_dep(v):
    P, S, N, n = v["P"], v["S"], int(round(v["N"])), v["n"]
    d = v["d"] if v["dsrc"] == "Given d" else 1 - (S / P) ** (1.0 / N)
    sl = (P - S) / N
    bvs = P - n * sl
    bvd = P * (1 - d) ** n
    rows = []
    for t in range(0, N + 1):
        if t == 0:
            rows.append(["0", "", "", "%.2f" % P, "", "", "%.2f" % P])
        else:
            bd = P * (1 - d) ** t
            rows.append([str(t), "%.2f" % sl, "%.2f" % (t * sl), "%.2f" % (P - t * sl),
                         "%.2f" % (P * (1 - d) ** (t - 1) * d), "%.2f" % (P - bd), "%.2f" % bd])
    return [O("Declining-balance rate used, d", "%.6f" % d), H("Straight-line (SLD)"),
            O("Charge per year  (P-S)/N", money(sl)), K("Book value at end of year n", money(bvs)),
            O("Accumulated depreciation, year n", money(n * sl)), H("Declining-balance (DBD)"),
            O("Charge in year n  BV(n-1) x d", money(P * (1 - d) ** (n - 1) * d)),
            K("Book value at end of year n  P(1-d)^n", money(bvd)),
            O("Accumulated depreciation, year n", money(P - bvd))], \
        (["Year", "SLD charge", "SLD accum", "SLD BV", "DBD charge", "DBD accum", "DBD BV"], rows)


reg("Other", "Depreciation (SLD and DBD)", "Book value, charge, accumulated, full schedule",
    [Field("P", "Purchase price, P ($)", "250000"), Field("S", "Salvage value, S ($)", "10000"),
     Field("N", "Useful life, N (years)", "6"),
     Field("dsrc", "Declining-balance rate d", opts=["From salvage value", "Given d"], kind="choice"),
     Field("d", "Given d (%) (if 'Given d')", "20", "pct"), Field("n", "Year n of interest", "4")],
    c_dep,
    "SLD: D = (P-S)/N     BV(n) = P - n(P-S)/N          DBD: BV(n) = P(1-d)^n     D(n) = BV(n-1) d\n"
    "d from salvage:  d = 1 - (S/P)^(1/N)",
    "Slide checks: P=14,000, S=3,000, N=7 -> SLD BV(1)=12,429, BV(4)=7,714 | d=20% (given): BV(1)=11,200, BV(4)=5,734, BV(7)=2,936.",
    active=a_dep, kw="book value sherbrooke")


def c_dep2(v):
    p, y, cv, h = v["p"], v["y"], v["cv"], v["h"]
    d = 1 - (cv / p) ** (1 / y)
    return [O("Estimated rate  d = 1 - (current/price)^(1/years)", "%.6f" % d), K("Projected book value at the horizon", money(p * (1 - d) ** h))], None


reg("Other", "Depreciation: Estimate d", "Find d from an observed value, then project (smokehouse)",
    [Field("p", "Original purchase price ($)", "245000"), Field("y", "Years since purchase", "3"),
     Field("cv", "Current value ($)", "180000"), Field("h", "Project value this many years after purchase", "20")],
    c_dep2, "d = 1 - (BV/P)^(1/n)        BV(h) = P(1-d)^h", "Slide check: d = 0.097663, value after 20 years = $31,372.")


# ---------- Varying rates ----------
def c_vary(v):
    rates, dep = v["rates"], v["dep"]
    n = len(rates)
    dep = dep + [0.0] * (n + 1 - len(dep))
    bal = dep[0]
    rows = [["0", "", "%.2f" % dep[0], "%.2f" % bal]]
    for t in range(1, n + 1):
        bal = bal * (1 + rates[t - 1]) + dep[t]
        rows.append([str(t), "%.4f%%" % (rates[t - 1] * 100), "%.2f" % dep[t], "%.2f" % bal])
    return [K("Balance at end of period %d" % n, money(bal)),
            N("Balance(t) = Balance(t-1) x (1 + rate of period t) + deposit(t).  Use negative numbers for withdrawals.")], \
        (["t", "Rate", "Deposit", "Balance"], rows)


reg("Other", "Varying Interest Rates", "Future value when the rate changes from period to period",
    [Field("rates", "Rate for periods 1,2,3... (% list)", "5, 6, 6, 4, 4", "plist", hint="One rate per period"),
     Field("dep", "Deposits at t=0,1,2,... (list)", "300, 0, 500, 0, 400", "list", hint="Missing entries count as 0")],
    c_vary, "Balance(t) = Balance(t-1)(1 + i_t) + deposit(t)",
    "The default numbers are PLACEHOLDERS (the slide example is an image) - type your own.", kw="different rates")


# ---------- Amanda Q2 ----------
def c_amanda(v):
    r, m, A1, G, Nq, g, Nw = v["r"], v["m"], v["A1"], v["G"], v["Nq"], v["g"], int(round(v["Nw"]))
    iq = r / m
    ie = (1 + iq) ** m - 1
    At = A1 + G * AG(iq, Nq)
    F = At * FA(iq, Nq)
    X = F / PAg(ie, g, Nw)
    rows = []
    for k in range(1, Nw + 1):
        w = X * (1 + g) ** (k - 1)
        rows.append(["Year %g" % (Nq / m + k), "%.2f" % w, "%.2f" % (w / (1 + ie) ** k)])
    return [H("Part 1: amount accumulated right after the last deposit"),
            O("Rate per deposit period  i_q = r/m", pc(iq, 4)), O("A_total = A' + G(A/G,i_q,N)", money(At)),
            K("Accumulated amount  F = A_total (F/A,i_q,N)", money(F)),
            O("Last deposit  A' + (N-1)G", money(A1 + (Nq - 1) * G)),
            H("Part 2: first annual withdrawal X"),
            O("Effective annual rate  i_e = (1+i_q)^m - 1", pc(ie, 4)),
            O("(P/A, g, i_e, N)", "%.5f" % PAg(ie, g, Nw)),
            K("First withdrawal  X = F / (P/A,g,i_e,N)", money(X)),
            O("Last withdrawal  X(1+g)^(N-1)", money(X * (1 + g) ** (Nw - 1)))], \
        (["Withdrawal", "Amount", "PW at end of deposits"], rows)


reg("Other", "Quarterly Gradient -> Annual Withdrawals", "Quiz 5 Q2 (Amanda): deposits with G, then growing withdrawals",
    [Field("r", "Nominal annual rate, r (%)", "6", "pct"), Field("m", "Compounding / deposit periods per year", "4"),
     Field("A1", "First deposit, A' ($)", "12000"), Field("G", "Gradient per deposit, G ($)", "600"),
     Field("Nq", "Number of deposits", "60"), Field("g", "Annual growth of withdrawals, g (%)", "2", "pct"),
     Field("Nw", "Number of annual withdrawals", "15")],
    c_amanda,
    "i_q = r/m   A_total = A' + G(A/G,i_q,N)   F = A_total(F/A,i_q,N)\n"
    "i_e = (1+i_q)^m - 1   F = X (P/A,g,i_e,Nw)  ->  X = F / (P/A,g,i_e,Nw)",
    "Defaults = Amanda: F = 2,603,162 and X = 239,737.", kw="amanda quiz 5 q2")


# ---------- Interest table ----------
def c_table(v):
    i, nmax = v["i"], min(int(v["Nmax"]), 600)
    rows = [[str(n), "%.5f" % FP(i, n), "%.5f" % PF(i, n), "%.5f" % AF(i, n), "%.5f" % FA(i, n),
             "%.5f" % AP(i, n), "%.5f" % PA(i, n), "%.5f" % AG(i, n)] for n in range(1, nmax + 1)]
    return [N("Appendix A style table for i = %s. Scroll with the mouse wheel." % pc(i, 4))], \
        (["N", "(F/P)", "(P/F)", "(A/F)", "(F/A)", "(A/P)", "(P/A)", "(A/G)"], rows)


reg("Other", "Interest Table (any rate)", "Appendix A table for any rate you type, N = 1..max",
    [Field("i", "Interest rate per period, i (%)", "5", "pct", hint="Any rate works, e.g. 0.492 or 4.67"),
     Field("Nmax", "Largest N to show", "40")],
    c_table, "Columns: (F/P) (P/F) (A/F) (F/A) (A/P) (P/A) (A/G)", kw="appendix lookup")


# ---------- Cheat sheet ----------
def c_cheat(v):
    L = [
        H("Factor formulas"),
        O("(F/P,i,N)", "(1+i)^N"), O("(P/F,i,N)", "1/(1+i)^N"), O("(A/F,i,N)", "i/((1+i)^N - 1)"),
        O("(F/A,i,N)", "((1+i)^N - 1)/i"), O("(A/P,i,N)", "i(1+i)^N/((1+i)^N - 1)"),
        O("(P/A,i,N)", "((1+i)^N - 1)/(i(1+i)^N)"), O("(A/G,i,N)", "1/i - N/((1+i)^N - 1)"),
        O("i° and (P/A,g,i,N)", "(1+i)/(1+g)-1 ;  (P/A,i°,N)/(1+g)"),
        O("Capitalized value", "P = A/i"), O("Capital recovery", "A = (P-S)(A/P,i,N) + S i"),
        O("Effective rate", "(1+r/m)^m - 1   |   e^r - 1"),
        H("Timing rules that cost marks"),
        N("1. Annuity: first payment at the END of period 1. A payment at t=0 is not part of the series. For a series that "
          "starts later, use (P/A,i,n) then discount (s-1) periods."),
        N("2. Arithmetic gradient: the textbook gradient is 0 at t=1 and G at t=2. For A' +/- G convert first: "
          "A_total = A' +/- G(A/G,i,N), then use A_total as an ordinary annuity."),
        N("3. Convert at the START of the series (one period before the first flow), then move to the time you need."),
        N("4. Geometric gradient: if g = i use P = N A/(1+g). If g > i, i° is negative - formula only, not in the tables."),
        N("5. Quarterly deposits with annual withdrawals: i_q = r/4 for deposits, but the EFFECTIVE annual rate "
          "(1+i_q)^4 - 1 for annual flows."),
        N("6. Bonds: coupon uses the stated coupon rate; discounting uses the market rate. Check whether the first coupon "
          "is NOW (t=0) or at the end of period 1."),
        N("7. 'How many years' answers: round UP to whole periods, but remember the last payment is smaller."),
        N("8. Table factors are rounded to 4-5 digits, so answers can differ from this program by a few cents/dollars. "
          "Report to the precision your prof asks for."),
    ]
    return L, None


reg("Other", "Formula Cheat Sheet", "All formulas and the timing rules that cost marks", [], c_cheat, "", kw="help formulas rules")

# =====================================================================
#  MODULE 4: COMPARISON METHODS (PW / AW / MARR)
# =====================================================================
def c_pwproj(v):
    P, A, S, n, i = v["P"], v["A"], v["S"], v["N"], v["i"]
    pa, pf = PA(i, n), PF(i, n)
    ben = A * pa + S * pf
    pw = -P + ben
    yrs = int(round(n))
    flows = [-P] + [A] * yrs
    if yrs >= 1:
        flows[-1] += S
    r = irr(flows) if yrs >= 1 else None
    dec = "ACCEPT  (PW > 0)" if pw > 1e-9 else ("REJECT  (PW < 0)" if pw < -1e-9 else "Marginally acceptable  (PW = 0)")
    return [K("Present worth  PW = -P + A(P/A,i,N) + S(P/F,i,N)", money(pw)),
            K("Decision at this MARR", dec),
            O("PW of the benefits (what investors would pay)", money(ben)),
            O("Annual worth  AW = PW (A/P,i,N)", money(pw * AP(i, n))),
            O("(P/A, i, N)", fac(pa)), O("(P/F, i, N)", fac(pf)),
            O("Break-even rate (IRR: the MARR where PW = 0)", pc(r) if r is not None else "none")], None


reg("Comparison", "PW of a Project (accept / reject)", "Module 4: PW at the MARR from first cost, net annual benefit, salvage",
    [Field("P", "First cost, P ($)", "650000"),
     Field("A", "Net annual benefit, A ($)  (benefits - costs)", "162500"),
     Field("S", "Salvage value, S ($)", "0"), Field("N", "Life, N (years)", "8"),
     Field("i", "MARR, i (%)", "15", "pct")],
    c_pwproj,
    "PW = -P + A(P/A,i,N) + S(P/F,i,N)      PW > 0 accept  |  PW < 0 reject  |  PW = 0 marginal\n"
    "If the benefit is a cost reduction:  A = old annual cost - new annual cost.",
    "Slide checks: XL Chemicals (defaults; 215,500 - 53,000 = 162,500) -> 79,187 (table) | Steve's network: 70,000 / 30,000 / 0 / 5 / 20% "
    "-> 19,718, investors would pay 89,718 | Margaret: 28,000 / 5,000 / 3,000 / 10 / 15% -> -2,164 | "
    "Automation: 15,000 / 2,800 (= 9,200 - 6,400) / 0 / 10 / 9% -> 2,970.",
    kw="marr accept reject independent module 4 present worth project xl chemicals steve margaret")


def _alt(P, A, S, n, i, L):
    cyc = -P + A * PA(i, n) + S * PF(i, n)
    reps = L // n
    pw = cyc * (1 + sum(PF(i, r * n) for r in range(1, reps)))
    aw = -P * AP(i, n) + A + S * AF(i, n)
    return pw, aw, reps


def c_compare(v):
    i = v["i"]
    n1, n2 = int(round(v["N1"])), int(round(v["N2"]))
    if n1 < 1 or n2 < 1:
        raise InputError("Both lives must be at least 1 year.")
    L = n1 * n2 // math.gcd(n1, n2)
    if L > 600:
        raise InputError("The lives need a common study period over 600 years - compare by annual worth by hand.")
    pw1, aw1, r1 = _alt(v["P1"], v["A1"], v["S1"], n1, i, L)
    pw2, aw2, r2 = _alt(v["P2"], v["A2"], v["S2"], n2, i, L)
    best = "Alternative 1" if pw1 > pw2 else ("Alternative 2" if pw2 > pw1 else "Tie")
    return [H("Study period"),
            O("Common study period (LCM of the two lives)", "%d years" % L),
            O("Repeats of Alt 1 / Alt 2 in that period", "%d / %d" % (r1, r2)),
            H("Present worth (largest wins)"),
            K("PW of Alternative 1", money(pw1)), K("PW of Alternative 2", money(pw2)),
            H("Annual worth (largest wins)"),
            O("AW of Alternative 1 (own life)", money(aw1)), O("AW of Alternative 2 (own life)", money(aw2)),
            K("Better alternative", best), O("Difference in PW", money(abs(pw1 - pw2))),
            N("Costs are negative numbers, so for a cost-only comparison the winner is the LEAST negative PW "
              "(= smallest present cost). Replacements repeat the first cost and salvage every N years.")], None


reg("Comparison", "Compare Two Alternatives (PW / AW)", "Mutually exclusive projects, equal or unequal lives (repeated to the LCM)",
    [Field("P1", "Alt 1: first cost, P1 ($)", "0"), Field("A1", "Alt 1: net annual benefit ($) (a cost = negative)", "-9200"),
     Field("S1", "Alt 1: salvage, S1 ($)", "0"), Field("N1", "Alt 1: life, N1 (years)", "10"),
     Field("P2", "Alt 2: first cost, P2 ($)", "15000"), Field("A2", "Alt 2: net annual benefit ($) (a cost = negative)", "-6400"),
     Field("S2", "Alt 2: salvage, S2 ($)", "0"), Field("N2", "Alt 2: life, N2 (years)", "10"),
     Field("i", "MARR, i (%)", "9", "pct")],
    c_compare,
    "PW = -P + A(P/A,i,N) + S(P/F,i,N)     AW = -P(A/P,i,N) + A + S(A/F,i,N)\n"
    "Unequal lives: repeat each alternative until the lives match (LCM), then compare PW.",
    "Slide check (materials handling): Alt 1 PW = -59,042.84, Alt 2 PW = -56,073.28 at 9%, 10 years -> Alt 2 is cheaper (table factors).",
    kw="mutually exclusive alternatives lcm unequal lives cost compare")


def c_profile(v):
    cf, i, top, step = v["cf"], v["i"], v["top"], v["step"]
    if len(cf) < 2:
        raise InputError("Enter at least two cash flows (t = 0, 1, ...).")
    if step <= 0 or top < 0:
        raise InputError("The step must be above 0 and the top rate must not be negative.")

    def pw(r):
        return cf[0] + sum(c * PF(r, t) for t, c in enumerate(cf) if t > 0)
    r0 = irr(cf)
    rows = []
    for k in range(min(int(top / step + 1e-9), 400) + 1):
        r = k * step
        x = pw(r)
        rows.append(["%g%%" % round(r * 100, 4), "%.2f" % x, "Accept" if x > 0 else ("Reject" if x < 0 else "Marginal")])
    x = pw(i)
    return [K("PW at the MARR", money(x)),
            K("Decision", "ACCEPT" if x > 1e-9 else ("REJECT" if x < -1e-9 else "Marginal")),
            O("Break-even interest rate (IRR)", pc(r0) if r0 is not None else "none"),
            O("PW at 0%  (just the sum of the flows)", money(sum(cf))),
            N("Accept while the MARR is below the break-even rate; reject above it.")], \
        (["i (%)", "PW(i)", "At this MARR"], rows)


reg("Comparison", "PW Profile and Break-even Rate", "PW at the MARR plus a table of PW at many rates (Tiger Machine Tool)",
    [Field("cf", "Cash flows at t=0,1,2,... (list)", "-75000, 24400, 27340, 55760", "list", wide=True,
           hint="Negative = cost, positive = receipt. Put 0 for years with no flow."),
     Field("i", "MARR, i (%)", "15", "pct"),
     Field("top", "Table runs up to (%)", "40", "pct"), Field("step", "Table step (%)", "2", "pct")],
    c_profile, "PW(i) = sum of CF(t)(P/F,i,t)       Break-even rate: the i where PW(i) = 0",
    "Slide checks: Tiger (defaults) -> PW(15%) = 3,553 | PW(0%) = 32,500 | PW(20%) = -3,412 | break-even 17.45%. "
    "'Extreme MARR': -100000, 50000, 50000 at 5% -> -7,029.",
    kw="profile irr break-even tiger machine extreme marr")


def c_marr(v):
    costs, rates, B = v["costs"], v["rates"], v["B"]
    n = len(costs)
    if n == 0 or len(rates) != n:
        raise InputError("Need the same number of costs and rates (you have %d costs and %d rates)." % (n, len(rates)))
    order = sorted(range(n), key=lambda k: -rates[k])
    cum, spent, ok, first_rej, last_ok, rows = 0.0, 0.0, True, None, None, []
    for rank, k in enumerate(order, 1):
        cum += costs[k]
        if ok and cum <= B + 1e-9:
            status, spent, last_ok = "Fund", cum, k
        else:
            if ok:
                ok, first_rej = False, k
            status = "Reject"
        rows.append([str(rank), str(k + 1), num(costs[k], 2), pc(rates[k], 2), num(cum, 2), status])
    funded = sum(1 for r in rows if r[5] == "Fund")
    outs = [K("Projects funded", "%d of %d" % (funded, n)), O("Total spent of the budget", "%s of %s" % (num(spent, 2), num(B, 2)))]
    if last_ok is not None:
        outs.append(O("Lowest rate still funded", pc(rates[last_ok], 2)))
    if first_rej is not None:
        outs.append(K("MARR = rate of the best project NOT funded", pc(rates[first_rej], 2)))
    outs.append(N("Projects are ranked by rate of return and funded until the budget runs out. The slides draw the MARR at the "
                  "first project the budget cannot pay for."))
    return outs, (["Rank", "Project #", "Cost", "Rate", "Cumulative", "Decision"], rows)


reg("Comparison", "MARR from a Budget (Independent Projects)", "Rank projects by rate of return, accumulate cost, find the cut-off",
    [Field("costs", "Project costs, in input order (list)", "150, 50, 50, 100, 200, 100, 200, 250, 300, 300, 400", "list", wide=True),
     Field("rates", "Rates of return (% list, same order)", "30, 45, 38, 40, 35, 28, 18, 25, 20, 10, 15", "plist", wide=True),
     Field("B", "Total budget available", "1200")],
    c_marr, "Sort by rate of return (highest first) -> cumulative cost -> fund until the budget is used -> MARR = next rate down",
    "Slide check (budget 1,200 in $1000): fund the 8 best projects (45% down to 20%) -> MARR 18%.",
    kw="marr budget hurdle rate independent ranking cumulative")


# =====================================================================
#  MODULE 3 (VII): NON-STANDARD ANNUITIES AND GRADIENTS
# =====================================================================
def c_nsann(v):
    A, i, k, N_ = v["A"], v["i"], int(round(v["k"])), int(round(v["N"]))
    if k < 1 or N_ < k or N_ % k:
        raise InputError("N must be a whole multiple of k (a payment every k periods).")
    cnt = N_ // k
    rows, tot = [], 0.0
    for j in range(1, cnt + 1):
        t = j * k
        f = FP(i, N_ - t)
        tot += A * f
        rows.append([str(t), "%.2f" % A, fac(f), "%.2f" % (A * f)])
    ie = (1 + i) ** k - 1
    m2 = A * FA(ie, cnt)
    a3 = A * AF(i, k)
    m3 = a3 * FA(i, N_)
    pw = a3 * PA(i, N_)
    return [H("Future value at the end of period %d" % N_),
            K("Method 2: change the rate  F = A(F/A, i_e, N/k)", money(m2)),
            O("Method 1: add up each payment separately", money(tot)),
            K("Method 3: equivalent regular annuity", money(m3)),
            O("Effective rate per k periods  i_e = (1+i)^k - 1", pc(ie, 4)),
            O("Equivalent payment every period  A(A/F,i,k)", money(a3)),
            H("Present worth"),
            K("PW at t = 0  =  A(A/F,i,k)(P/A,i,N)", money(pw))], \
        (["t", "Payment", "(F/P,i,N-t)", "Value at N"], rows)


reg("Basics", "Non-Standard Annuity (every k periods)", "Payment every k periods but compounding every period: all 3 methods",
    [Field("A", "Payment, A ($)", "1000"), Field("i", "Interest rate per compounding period, i (%)", "4", "pct"),
     Field("k", "A payment every k periods, k", "4"), Field("N", "Total periods, N (multiple of k)", "20")],
    c_nsann,
    "M1: F = sum of A(F/P,i,N-t)      M2: i_e = (1+i)^k - 1,  F = A(F/A,i_e,N/k)\n"
    "M3: A_eq = A(A/F,i,k),  F = A_eq(F/A,i,N),  PW = A_eq(P/A,i,N)",
    "Slide checks: $1000 every 4 years for 20 years at 4% -> 7,013 (methods 1 and 2), 7,012 with table factors (method 3). "
    "Airport runway: 5,000,000 every 3 years, 60 years, 5% -> PW about 30,022,340.",
    kw="non standard nonstandard maintenance runway every fourth year periodic")


def c_daily(v):
    C, ppl, r, d, W = v["C"], v["ppl"], v["r"], int(round(v["d"])), int(round(v["W"]))
    if not 1 <= d <= 7 or W < 1:
        raise InputError("Deposit days per week must be 1 to 7, and weeks must be at least 1.")
    amt = C * ppl
    idv = r / 365.0
    iw = (1 + idv) ** 7 - 1
    wk = amt * (1 + idv) ** (8 - d) * FA(idv, d)
    F = wk * FA(iw, W)
    return [O("Contribution per day (all people)", money(amt)),
            O("Interest rate per day  r/365", pc(idv, 6)),
            O("Effective rate per week  (1 + r/365)^7 - 1", pc(iw, 5)),
            O("One week's deposits, valued at the end of that week", money(wk)),
            O("(F/A, i_week, W)", fac(FA(iw, W))),
            K("Total saved when withdrawn (start of week W+1)", money(F)),
            O("Total contributed (no interest)", money(amt * d * W))], None


reg("Basics", "Weekday Deposits, Daily Interest", "Deposit d days a week, interest every day (class-party savings)",
    [Field("C", "Deposit per person per day ($)", "0.25"), Field("ppl", "Number of people", "90"),
     Field("r", "Nominal annual rate, compounded daily (365), r (%)", "8", "pct"),
     Field("d", "Deposit days per week (Mon first, start of day)", "5"), Field("W", "Number of full weeks of deposits", "14")],
    c_daily,
    "Week value (Sunday evening) = C(1+r/365)^(8-d)(F/A,r/365,d)       i_week = (1+r/365)^7 - 1       F = week value (F/A,i_week,W)",
    "Slide check (EE class party): defaults -> about $1,592.56.", kw="class party weekly daily weekday")


def c_salgrow(v):
    sal, sp, g, r, m, N_ = v["sal"], v["sp"], v["g"], v["r"], int(round(v["m"])), int(round(v["N"]))
    X, xs, xm = v["X"], v["xs"], v["xm"]
    if m < 1 or N_ < 1:
        raise InputError("Deposits per year and years must be at least 1.")
    iq = r / m
    ie = (1 + iq) ** m - 1
    dep = sal / m * sp
    y1 = dep * FA(iq, m)
    f = PAg(ie, g, N_)
    P0 = y1 * f
    Fs = P0 * FP(ie, N_)
    xd = X * xs
    Fx = xd * FA(ie, N_) * (1 + iq) ** (m - xm)
    return [H("Salary savings (geometric gradient from year to year)"),
            O("Deposit each period in year 1", money(dep)),
            O("Rate per deposit period  r/m", pc(iq, 4)),
            O("Year 1 deposits accumulated at end of year 1  A(F/A,i,m)", money(y1)),
            O("Effective annual rate  i_e = (1+r/m)^m - 1", pc(ie, 4)),
            O("(P/A, g, i_e, N)", "%.5f" % f),
            O("Present worth of the yearly totals", money(P0)),
            K("Salary savings at end of year N", money(Fs)),
            H("Yearly gift (annuity every year)"),
            O("Amount saved from each gift", money(xd)),
            K("Gift savings at end of year N", money(Fx)),
            H("Total"), K("Total saved at the end of year N", money(Fs + Fx))], None


reg("Gradients", "Monthly Savings + Yearly Raise + Yearly Gift", "Example A: monthly deposits, salary rises g% a year, plus a yearly gift",
    [Field("sal", "Take-home pay in year 1 ($ per year)", "66000"), Field("sp", "Portion of salary saved (%)", "10", "pct"),
     Field("g", "Yearly raise, g (%)", "3", "pct"), Field("r", "Nominal annual rate, r (%)", "12", "pct"),
     Field("m", "Deposits and compounding per year, m", "12"), Field("N", "Number of years, N", "5"),
     Field("X", "Yearly gift ($)", "2000"), Field("xs", "Portion of the gift saved (%)", "50", "pct"),
     Field("xm", "Gift arrives at the end of month", "6", hint="June = 6")],
    c_salgrow,
    "A1 = deposit(F/A,i,m)    i_e = (1+i)^m - 1    P0 = A1(P/A,g,i_e,N)    F = P0(F/P,i_e,N)\n"
    "Gift: F = X(F/A,i_e,N)(1+i)^(m - month)",
    "Slide check (Example A): defaults -> salary 47,361 + gifts 6,836 = 54,197.", kw="example a salary raise birthday gift retirement")


# =====================================================================
#  ANSWER FINDER: type every number you were given + the answer choices
# =====================================================================
FUNS = {"FP": FP, "PF": PF, "AF": AF, "FA": FA, "AP": AP, "PA": PA, "AG": AG}
FLBL = {"FP": "F/P", "PF": "P/F", "AF": "A/F", "FA": "F/A", "AP": "A/P", "PA": "P/A", "AG": "A/G"}
MSET = (2, 4, 12, 52, 365)


def _ps(i):
    s = ("%.4f" % (i * 100)).rstrip("0").rstrip(".")
    return (s or "0") + "%"


def _fn(x):
    if x == 0:
        return "0"
    d = max(0, 4 - int(math.floor(math.log10(abs(x)))))
    return "%.*f" % (min(d, 8), x)


def _astr(a):
    return ("{:,.4f}".format(a)).rstrip("0").rstrip(".")


def _valstr(kind, x):
    return {"$": money, "%": lambda z: pc(z, 4), "n": num, "f": _fn}[kind](x)


def _cv(kind, x):
    return x * 100.0 if kind == "%" else x


def mk(parts):
    """Recipe from parts [(sign, amount, [(factor, i, n), ...]), ...]; fn(txt) -> (value, text or None)."""
    def fn(txt):
        tot, s1, s2 = 0.0, [], []
        for k, (sg, amt, fl) in enumerate(parts):
            prod = amt
            fsym = fnu = ""
            for nm, i, n in fl:
                f = FUNS[nm](i, n)
                prod *= f
                if txt:
                    fsym += "(%s,%s,%g)" % (FLBL[nm], _ps(i), n)
                    fnu += "(%s)" % _fn(f)
            tot += sg * prod
            if txt:
                eff = sg if amt >= 0 else -sg
                sgn = ("-" if eff < 0 else "") if k == 0 else (" - " if eff < 0 else " + ")
                s1.append(sgn + money(abs(amt)) + fsym)
                s2.append(sgn + money(abs(amt)) + fnu)
        return tot, (("%s = %s = %s" % ("".join(s1), "".join(s2), money(tot))) if txt else None)
    return fn


def _cu(val, text):
    """Formula-only recipe (no table factors): val() -> number, text(x) -> string."""
    def fn(txt):
        x = val()
        return x, (text(x) if txt else None)
    return fn


def _facfn(nm, i, n):
    def fn(txt):
        x = FUNS[nm](i, n)
        return x, ("(%s,%s,%g) = %s" % (FLBL[nm], _ps(i), n, _fn(x)) if txt else None)
    return fn


def _geo(X, i, g, n, fut):
    def fn(txt):
        z = I0(i, g)
        f = PAg(i, g, n)
        x = X * f * (FP(i, n) if fut else 1.0)
        if not txt:
            return x, None
        return x, "i° = (1+i)/(1+g) - 1 = %s ;  (P/A,g,i,N) = (P/A,i°,N)/(1+g) = %s ;  %s = %s" % (
            pc(z, 4), fac(f), "F = A(P/A,g,i,N)(F/P,i,N)" if fut else "P = A(P/A,g,i,N)", money(x))
    return fn


def finder_recipes(am, rs, ns, pairs_ok, triples_ok):
    R = []

    def add(kind, title, fn):
        R.append((kind, title, fn))

    ints = [int(n) for n in ns if n == int(n) and n > 1]
    ms = sorted(set(MSET) | set(ints))

    def ctx(i, n):
        return "@%s, N=%g" % (_ps(i), n)

    # ---- rate conversions
    for r in rs:
        for m in ms:
            add("%", "Effective rate from nominal %s, m=%d" % (_ps(r), m),
                _cu(lambda r=r, m=m: (1 + r / m) ** m - 1,
                    lambda x, r=r, m=m: "i_e = (1 + r/m)^m - 1 = (1 + %s/%d)^%d - 1 = %s" % (_ps(r), m, m, pc(x, 4))))
            add("%", "Nominal rate from effective %s, m=%d" % (_ps(r), m),
                _cu(lambda r=r, m=m: m * ((1 + r) ** (1.0 / m) - 1),
                    lambda x, r=r, m=m: "r = m[(1+i_e)^(1/m) - 1] = %d[(1 + %s)^(1/%d) - 1] = %s" % (m, _ps(r), m, pc(x, 4))))
        add("%", "Continuous: effective rate from nominal %s" % _ps(r),
            _cu(lambda r=r: math.exp(r) - 1, lambda x, r=r: "i_e = e^r - 1 = e^%s - 1 = %s" % (_ps(r), pc(x, 4))))
        add("%", "Continuous: nominal rate from effective %s" % _ps(r),
            _cu(lambda r=r: math.log(1 + r), lambda x, r=r: "r = ln(1 + i_e) = ln(1 + %s) = %s" % (_ps(r), pc(x, 4))))
        add("n", "Doubling time, compound @%s" % _ps(r),
            _cu(lambda r=r: math.log(2) / math.log(1 + r), lambda x, r=r: "N = ln2 / ln(1+i) = %s" % num(x)))
        for m, k in ((2, 1), (4, 1), (12, 1), (4, 2), (12, 4), (12, 2), (12, 6), (365, 52)):
            add("%", "Rate per payment period: r=%s, m=%d, k=%d" % (_ps(r), m, k),
                _cu(lambda r=r, m=m, k=k: (1 + r / m) ** (m / float(k)) - 1,
                    lambda x, r=r, m=m, k=k: "i = (1 + r/m)^(m/k) - 1 = (1 + %s/%d)^(%d/%d) - 1 = %s" % (_ps(r), m, m, k, pc(x, 4))))
        for g in rs:
            if g != r:
                add("%", "Growth-adjusted rate i° (i=%s, g=%s)" % (_ps(r), _ps(g)),
                    _cu(lambda r=r, g=g: I0(r, g),
                        lambda x, r=r, g=g: "i° = (1+i)/(1+g) - 1 = (1+%s)/(1+%s) - 1 = %s" % (_ps(r), _ps(g), pc(x, 5))))

    # ---- the factors themselves
    for i in rs:
        for n in ns:
            for nm in FUNS:
                add("f", "Factor (%s, %s, N=%g)" % (FLBL[nm], _ps(i), n), _facfn(nm, i, n))

    # ---- one dollar amount
    SINGLE = (("F given P", ("FP",)), ("P given F", ("PF",)), ("A given F", ("AF",)), ("F given A", ("FA",)),
              ("A given P", ("AP",)), ("P given A", ("PA",)), ("A given G", ("AG",)),
              ("P given G", ("AG", "PA")), ("F given G", ("AG", "FA")))
    for X in am:
        xs = money(X)
        for i in rs:
            add("$", "Capitalized value A/i  [%s @%s]" % (xs, _ps(i)),
                _cu(lambda X=X, i=i: X / i, lambda x, X=X, i=i: "P = A/i = %s / %s = %s" % (money(X), _ps(i), money(x))))
            for n in ns:
                c = ctx(i, n)
                for lab, fl in SINGLE:
                    add("$", "%s  [%s %s]" % (lab, xs, c), mk([(1, X, [(nm, i, n) for nm in fl])]))
                add("$", "Simple interest F=P(1+iN)  [%s %s]" % (xs, c),
                    _cu(lambda X=X, i=i, n=n: X * (1 + i * n),
                        lambda x, X=X, i=i, n=n: "F = P(1 + iN) = %s(1 + %s x %g) = %s" % (money(X), _ps(i), n, money(x))))
                for k in ns:
                    if k != n:
                        add("$", "P given A, %g payments after a delay of %g  [%s @%s]" % (n, k, xs, _ps(i)),
                            mk([(1, X, [("PA", i, n), ("PF", i, k)])]))
                for g in rs:
                    if g != i:
                        add("$", "P, geometric gradient  [A=%s, i=%s, g=%s, N=%g]" % (xs, _ps(i), _ps(g), n), _geo(X, i, g, n, False))
                        add("$", "F, geometric gradient  [A=%s, i=%s, g=%s, N=%g]" % (xs, _ps(i), _ps(g), n), _geo(X, i, g, n, True))
            for k in ns:
                for N_ in ns:
                    if k < N_ and k == int(k) and N_ == int(N_) and int(N_) % int(k) == 0:
                        add("$", "F, payment every %g periods for %g  [%s @%s]" % (k, N_, xs, _ps(i)),
                            mk([(1, X, [("AF", i, k), ("FA", i, N_)])]))
                        add("$", "PW, payment every %g periods for %g  [%s @%s]" % (k, N_, xs, _ps(i)),
                            mk([(1, X, [("AF", i, k), ("PA", i, N_)])]))

    # ---- two amounts
    if pairs_ok:
        PAIRS = (
            ("PW: first cost %s, annual benefit %s %s", lambda X, Y, i, n: [(-1, X, []), (1, Y, [("PA", i, n)])]),
            ("AW: first cost %s, annual benefit %s %s", lambda X, Y, i, n: [(-1, X, [("AP", i, n)]), (1, Y, [])]),
            ("Capital recovery: price %s, salvage %s %s", lambda X, Y, i, n: [(1, X, [("AP", i, n)]), (-1, Y, [("AF", i, n)])]),
            ("Max price: savings %s/yr, salvage %s %s", lambda X, Y, i, n: [(1, X, [("PA", i, n)]), (1, Y, [("PF", i, n)])]),
        )
        for a in range(len(am)):
            for b in range(len(am)):
                if a == b:
                    continue
                X, Y = am[a], am[b]
                xs, ys = money(X), money(Y)
                for i in rs:
                    for n in ns:
                        for lab, bld in PAIRS:
                            add("$", lab % (xs, ys, ctx(i, n)), mk(bld(X, Y, i, n)))
                    add("$", "Perpetuity + first cost: %s + %s / %s" % (ys, xs, _ps(i)),
                        _cu(lambda X=X, Y=Y, i=i: Y + X / i,
                            lambda x, X=X, Y=Y, i=i: "PW = first cost + A/i = %s + %s/%s = %s" % (money(Y), money(X), _ps(i), money(x))))
                    add("n", "Periods N from P=%s, F=%s @%s" % (xs, ys, _ps(i)),
                        _cu(lambda X=X, Y=Y, i=i: math.log(Y / X) / math.log(1 + i),
                            lambda x, X=X, Y=Y, i=i: "N = ln(F/P)/ln(1+i) = %s" % num(x)))
                for n in ns:
                    add("%", "Rate i from P=%s, F=%s, N=%g" % (xs, ys, n),
                        _cu(lambda X=X, Y=Y, n=n: (Y / X) ** (1.0 / n) - 1,
                            lambda x, X=X, Y=Y, n=n: "i = (F/P)^(1/N) - 1 = (%s/%s)^(1/%g) - 1 = %s" % (money(Y), money(X), n, pc(x, 4))))
                    add("$", "Straight-line depreciation per year (P=%s, S=%s, N=%g)" % (xs, ys, n),
                        _cu(lambda X=X, Y=Y, n=n: (X - Y) / n,
                            lambda x, X=X, Y=Y, n=n: "D = (P - S)/N = (%s - %s)/%g = %s" % (money(X), money(Y), n, money(x))))
                    add("%", "Declining-balance rate d (P=%s, S=%s, N=%g)" % (xs, ys, n),
                        _cu(lambda X=X, Y=Y, n=n: 1 - (Y / X) ** (1.0 / n),
                            lambda x, X=X, Y=Y, n=n: "d = 1 - (S/P)^(1/N) = %s" % pc(x, 4)))

    # ---- three amounts
    if triples_ok:
        TRIPS = (
            ("PW: first cost %s, annual benefit %s, salvage %s %s",
             lambda X, Y, Z, i, n: [(-1, X, []), (1, Y, [("PA", i, n)]), (1, Z, [("PF", i, n)])]),
            ("AW: first cost %s, annual benefit %s, salvage %s %s",
             lambda X, Y, Z, i, n: [(-1, X, [("AP", i, n)]), (1, Y, []), (1, Z, [("AF", i, n)])]),
            ("PW: first cost %s, benefit %s/yr, cost %s/yr %s",
             lambda X, Y, Z, i, n: [(-1, X, []), (1, Y, [("PA", i, n)]), (-1, Z, [("PA", i, n)])]),
            ("AW: first cost %s, benefit %s/yr, cost %s/yr %s",
             lambda X, Y, Z, i, n: [(-1, X, [("AP", i, n)]), (1, Y, []), (-1, Z, [])]),
        )
        L = len(am)
        for a in range(L):
            for b in range(L):
                for d in range(L):
                    if a == b or a == d or b == d:
                        continue
                    X, Y, Z = am[a], am[b], am[d]
                    for i in rs:
                        for n in ns:
                            for lab, bld in TRIPS:
                                add("$", lab % (money(X), money(Y), money(Z), ctx(i, n)), mk(bld(X, Y, Z, i, n)))

    # ---- the amounts as a cash-flow stream
    if 2 <= len(am) <= 12:
        for i in rs:
            parts = [(1, c, [("PF", i, t)] if t > 0 else []) for t, c in enumerate(am)]
            add("$", "PW of your amounts as cash flows at t=0,1,2,... @%s" % _ps(i), mk(parts))
    return R


def c_finder(v):
    base = list(v["rates"])[:6]
    am = list(v["am"])[:8]
    ns = [n for n in v["ns"] if n > 0][:6]
    ans = [a for a in v["ans"] if a != 0][:12]
    tol = (abs(v["tol"]) or 2.0) / 100.0
    rs, notes = list(base), []
    if v["split"].startswith("Yes") and len(base) <= 3:
        rs += [r / m for r in base for m in MSET]
        notes.append("Also tried each rate split as r/2, r/4, r/12, r/52 and r/365 (nominal rate -> rate per period).")
    if CONT[0]:
        rs += [math.exp(r) - 1 for r in base]
        notes.append("Continuous compounding is ON: also tried i = e^r - 1 for each rate.")
    seen, uniq = set(), []
    for r in rs:
        key = round(r, 12)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    rs = uniq[:24]
    est = max(1, len(rs) * len(ns))
    k2 = len(am) * (len(am) - 1)
    k3 = k2 * max(0, len(am) - 2)
    recs = finder_recipes(am, rs, ns, k2 * est * 5 <= 60000, k3 * est * 4 <= 40000)
    outs = [O("Calculations tried", "{:,}".format(len(recs))), O("A choice matches if within", "%g%%" % (tol * 100))]
    outs += [N(t) for t in notes]
    if not ans:
        outs.append(N("Type the answer choices from the question in 'Answer choices' and this lists which of them your numbers can produce."))
        return outs, None
    hits = [[] for _ in ans]
    try:
        for idx, (kind, title, fn) in enumerate(recs):
            vals = []
            for mode in (False, True):
                TBL[0], MISS[0] = mode, False
                try:
                    x = fn(False)[0]
                    good = math.isfinite(x) and not (mode and MISS[0])
                except Exception:
                    x, good = None, False
                vals.append(x if good else None)
            ve, vt = vals
            if ve is None and vt is None:
                continue
            for ai, a in enumerate(ans):
                modes, best, cv0 = [], None, 0.0
                for tag, x in (("eq", ve), ("tb", vt)):
                    if x is None or (tag == "tb" and ve is not None and x == ve):
                        continue
                    cv = _cv(kind, x)
                    d = abs(abs(cv) - abs(a)) / abs(a)
                    if d <= tol:
                        modes.append(tag)
                        if best is None or d < best:
                            best, cv0 = d, cv
                if modes:
                    hits[ai].append((best, idx, modes, (cv0 < 0) != (a < 0), ve, vt))
        for ai, a in enumerate(ans):
            hs = sorted(hits[ai])
            outs.append(H("Choice %d:  %s    (%d match%s)" % (ai + 1, _astr(a), len(hs), "" if len(hs) == 1 else "es")))
            if not hs:
                outs.append(N("Nothing from these numbers lands within the window. Check that every rate, N and dollar amount "
                              "from the question is entered (or try 'Also try nominal splits')."))
                continue
            for rank, (d, idx, modes, flip, ve, vt) in enumerate(hs[:10]):
                kind, title, fn = recs[idx]
                te = tt = None
                TBL[0], MISS[0] = False, False
                try:
                    ve, te = fn(True)
                except Exception:
                    ve = None
                TBL[0], MISS[0] = True, False
                try:
                    vt, tt = fn(True)
                except Exception:
                    vt = None
                miss = MISS[0]
                TBL[0] = False
                shown = ve if ve is not None else vt
                val = _valstr(kind, shown)
                same = vt is not None and ve is not None and abs(vt - ve) <= 1e-12 * max(1.0, abs(ve))
                if vt is not None and not miss and not same and ve is not None:
                    val += "  |  table " + _valstr(kind, vt)
                outs.append((K if rank == 0 else O)(title + ("  (sign differs)" if flip else ""), val))
                if te is not None:
                    outs.append(N("Equation:  %s     [%.2f%% off]" % (te, abs(abs(_cv(kind, ve)) - abs(a)) / abs(a) * 100)))
                if vt is None or miss:
                    outs.append(N("Table:  not applicable (formula only, or this rate / N is not in the printed tables)"))
                elif same:
                    outs.append(N("Table:  same value as the equation"))
                else:
                    outs.append(N("Table:  %s     [%.2f%% off]" % (tt, abs(abs(_cv(kind, vt)) - abs(a)) / abs(a) * 100)))
            if len(hs) > 10:
                outs.append(N("... and %d more within the window (closest 10 shown)." % (len(hs) - 10)))
    finally:
        TBL[0] = False
    return outs, None


reg("Answer Finder", "Answer Finder (multiple choice)", "Enter every number you were given + the answer choices; see which choices your numbers can make",
    [Field("rates", "Rates given (% list): interest, growth, nominal...", "15", "plist", wide=True,
           hint="Percent numbers, e.g. 8, 3, 6/12. Used as i, g or r."),
     Field("am", "Dollar amounts given (list): P, F, A, G, S, costs...", "28000, 5000, 3000", "list", wide=True,
           hint="No thousands commas: type 25094, not 25,094. Negative numbers are fine."),
     Field("ns", "Periods given (list): N, k, years, payments per year...", "10", "list", wide=True),
     Field("ans", "Answer choices (list)", "-2164, 25094, 5800, 12000", "list", wide=True,
           hint="Type every choice. Rates answers go in percent (27.11 for 27.11%)."),
     Field("tol", "Count a choice as matched if within (%)", "2"),
     Field("split", "Also try nominal splits (r/12, r/4...)", opts=["No", "Yes"], kind="choice")],
    c_finder,
    "Tries factors, P/F/A/G conversions, PW / AW with salvage, capital recovery, perpetuities, geometric gradients, "
    "non-standard annuities, effective / nominal / continuous rates and i from P and F.",
    "Each match shows the EQUATION value and the TABLE value (factors rounded to 5 significant digits, like Appendix A). "
    "Table values only appear when the rate is a table rate (multiples of 0.25%) and N is 1 to 100. "
    "Defaults = Margaret's project: -2,164 is the PW and 25,094 is 5,000(P/A,15%,10).",
    notable=True, kw="answer finder multiple choice check solver which options within 2 percent")


CATS = ["Answer Finder", "Basics", "Gradients", "Capital & Rates", "Loans & Bonds", "Comparison", "Other"]

# =====================================================================
#  UI
# =====================================================================
NAVY = (31, 56, 100)
BG = (244, 246, 250)
WHITE = (255, 255, 255)
INK = (28, 32, 40)
GREY = (110, 118, 130)
LINE = (200, 206, 216)
INPUT_BG = (255, 250, 205)
INPUT_FOCUS = (255, 241, 150)
KEY_BG = (226, 240, 217)
ERR = (190, 40, 40)
NOTE = (70, 90, 120)
HOVER = (222, 232, 250)


def make_font(names, size, bold=False, italic=False):
    return pygame.font.SysFont(names, size, bold=bold, italic=italic)


class Fonts:
    def __init__(self):
        ui = "segoeui,helveticaneue,arial,dejavusans,liberationsans"
        mono = "consolas,menlo,couriernew,dejavusansmono,liberationmono"
        self.title = make_font(ui, 26, True)
        self.h1 = make_font(ui, 18, True)
        self.ui = make_font(ui, 16)
        self.uib = make_font(ui, 16, True)
        self.small = make_font(ui, 14)
        self.smalli = make_font(ui, 14, italic=True)
        self.mono = make_font(mono, 15)
        self.monos = make_font(mono, 14)
        self.monob = make_font(mono, 15, True)


def wrap(text, font, width):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            t = (cur + " " + w).strip() if cur else w
            if font.size(t)[0] <= width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def clip_text(font, text, width):
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text + "..."


class ScrollBar:
    """Draggable website-style scrollbar (drag the thumb or click the track)."""

    def __init__(self):
        self.track = None
        self.thumb = None
        self.drag = None

    def draw(self, surf, track, scroll, maxs):
        self.track, self.thumb = track, None
        if maxs <= 0:
            return
        pygame.draw.rect(surf, (226, 230, 238), track, border_radius=5)
        h = max(34, int(track.h * track.h / (track.h + maxs)))
        y = track.y + int((track.h - h) * min(1.0, scroll / maxs))
        self.thumb = pygame.Rect(track.x, y, track.w, h)
        hot = self.drag is not None or self.thumb.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, (110, 124, 150) if hot else (160, 170, 190), self.thumb, border_radius=5)

    def _to(self, my, maxs):
        span = max(1, self.track.h - self.thumb.h)
        frac = (my - self.drag - self.track.y) / span
        return int(max(0.0, min(1.0, frac)) * maxs)

    def event(self, e, scroll, maxs):
        """Returns a new scroll value if the bar used the event, else None."""
        if self.thumb is None or maxs <= 0:
            self.drag = None
            return None
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.track.inflate(8, 0).collidepoint(e.pos):
            if self.thumb.collidepoint(e.pos):
                self.drag = e.pos[1] - self.thumb.y
                return scroll
            self.drag = self.thumb.h // 2
            return self._to(e.pos[1], maxs)
        if e.type == pygame.MOUSEMOTION and self.drag is not None:
            return self._to(e.pos[1], maxs)
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1 and self.drag is not None:
            self.drag = None
            return scroll
        return None


CONT_RECT = [None]


def draw_cont_button(surf, f, right, y):
    on = CONT[0]
    txt = f.small.render("Continuous compounding: %s   (Ctrl+K)" % ("ON" if on else "OFF"), True, WHITE if on else NAVY)
    r = pygame.Rect(0, y, txt.get_width() + 20, 24)
    r.right = right
    pygame.draw.rect(surf, (46, 140, 80) if on else (222, 232, 250), r, border_radius=6)
    pygame.draw.rect(surf, WHITE, r, 1, border_radius=6)
    surf.blit(txt, (r.x + 10, r.y + 3))
    CONT_RECT[0] = r


def toggle_cont(screens):
    CONT[0] = not CONT[0]
    for s in screens.values():
        s.recalc()


class CalcScreen:
    def __init__(self, calc, fonts):
        self.c, self.f = calc, fonts
        self.scroll = 0
        self.content_h = 0
        self.view_h = 400
        self.maxs = 0
        self.bar = ScrollBar()
        self.reset()

    # ---- state
    def reset(self):
        self.text, self.cur, self.choice = {}, {}, {}
        for fl in self.c.fields:
            if fl.kind == "choice":
                self.choice[fl.key] = 0
            else:
                self.text[fl.key] = fl.default
                self.cur[fl.key] = len(fl.default)
        self.focus = 0
        self.fresh = True
        self.act = None
        self.outs, self.table, self.err = [], None, None
        self.scroll = 0
        self.recalc()
        self.focus = self.first_active()

    def first_active(self):
        for k, fl in enumerate(self.c.fields):
            if self.is_active(fl):
                return k
        return 0

    def is_active(self, fl):
        return self.act is None or fl.key in self.act or fl.kind == "choice"

    def choices(self):
        return {fl.key: fl.opts[self.choice[fl.key]] for fl in self.c.fields if fl.kind == "choice"}

    def recalc(self):
        v = dict(self.choices())
        try:
            self.act = self.c.active(v) if self.c.active else None
        except Exception:
            self.act = None
        self.err = None
        conv = []   # rates converted with e^r - 1 in continuous mode
        for fl in self.c.fields:
            if fl.kind == "choice":
                continue
            if self.act is not None and fl.key not in self.act:
                v[fl.key] = [] if fl.kind in ("list", "plist") else 0.0
                continue
            try:
                if fl.kind == "list":
                    v[fl.key] = parse_list(self.text[fl.key])
                elif fl.kind == "plist":
                    v[fl.key] = [x / 100.0 for x in parse_list(self.text[fl.key])]
                    if CONT[0] and fl.cont:
                        v[fl.key] = [math.exp(x) - 1 for x in v[fl.key]]
                elif fl.kind == "pct":
                    r = parse_num(self.text[fl.key]) / 100.0
                    if CONT[0] and (fl.key == "i" or fl.cont):
                        conv.append(r)
                        r = math.exp(r) - 1
                    v[fl.key] = r
                else:
                    v[fl.key] = parse_num(self.text[fl.key])
            except Exception:
                self.err = "Can't read the value for:  " + fl.label
                self.outs, self.table = [], None
                return
        # choice keys: some functions want the full option text, some index 0 char
        try:
            TBL[0] = False
            self.outs, self.table = self.c.fn(v)
            if not self.c.notable:
                self._add_table_values(v)
            if conv:
                pre = [N("Continuous compounding is ON: the rate typed is a nominal continuous rate r, "
                         "so i = e^r - 1 is used in every factor.")]
                pre += [O("i = e^r - 1   (r = %s)" % pc(r, 4), pc(math.exp(r) - 1, 5)) for r in conv[:1]]
                self.outs = pre + self.outs
        except InputError as e:
            self.err, self.outs, self.table = str(e), [], None
        except ZeroDivisionError:
            self.err, self.outs, self.table = "Division by zero - check the inputs (a rate or N of 0?)", [], None
        except (ValueError, OverflowError):
            self.err, self.outs, self.table = "These inputs have no valid answer - check them.", [], None
        except Exception as e:  # never crash the exam
            self.err, self.outs, self.table = "Error: %s" % e, [], None

    def _add_table_values(self, v):
        """Run the calculator again with every factor rounded like the printed tables and
        remember the table answer next to each equation answer."""
        TBL[0] = True
        try:
            outs_t, _ = self.c.fn(v)
        except Exception:
            outs_t = []
        finally:
            TBL[0] = False
        if len(outs_t) == len(self.outs):
            for o, ot in zip(self.outs, outs_t):
                if o.style in ("key", "norm") and ot.label == o.label and ot.value != o.value:
                    o.tv = ot.value

    def move_focus(self, d):
        n = len(self.c.fields)
        if not n:
            return
        k = self.focus
        for _ in range(n):
            k = (k + d) % n
            if self.is_active(self.c.fields[k]):
                break
        self.focus, self.fresh = k, True

    # ---- events
    def on_event(self, e, rect_cache):
        s = self.bar.event(e, self.scroll, self.maxs)
        if s is not None:
            self.scroll = s
            return None
        fl = self.c.fields[self.focus] if self.c.fields else None
        if e.type == pygame.MOUSEWHEEL:
            self.scroll = max(0, min(max(0, self.content_h - self.view_h), self.scroll - e.y * 48))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for k, (r, left_half) in rect_cache.items():
                if r.collidepoint(e.pos):
                    fld = self.c.fields[k]
                    if not self.is_active(fld):
                        return None
                    self.focus, self.fresh = k, True
                    if fld.kind == "choice":
                        self.cycle(fld, -1 if e.pos[0] < r.centerx else 1)
                    return None
        elif e.type == pygame.TEXTINPUT and fl and fl.kind != "choice":
            ch = "".join(c for c in e.text if c.isprintable())
            if self.fresh:
                self.text[fl.key], self.cur[fl.key], self.fresh = "", 0, False
            t, c = self.text[fl.key], self.cur[fl.key]
            self.text[fl.key] = t[:c] + ch + t[c:]
            self.cur[fl.key] = c + len(ch)
            self.recalc()
        elif e.type == pygame.KEYDOWN:
            mods = e.mod
            if e.key == pygame.K_ESCAPE:
                return "menu"
            if e.key == pygame.K_r and mods & pygame.KMOD_CTRL:
                self.reset()
            elif e.key == pygame.K_TAB:
                self.move_focus(-1 if mods & pygame.KMOD_SHIFT else 1)
            elif e.key in (pygame.K_DOWN, pygame.K_RETURN, pygame.K_KP_ENTER):
                self.move_focus(1)
            elif e.key == pygame.K_UP:
                self.move_focus(-1)
            elif e.key == pygame.K_PAGEDOWN:
                self.scroll = min(max(0, self.content_h - self.view_h), self.scroll + self.view_h - 40)
            elif e.key == pygame.K_PAGEUP:
                self.scroll = max(0, self.scroll - self.view_h + 40)
            elif fl and fl.kind == "choice":
                if e.key in (pygame.K_LEFT,):
                    self.cycle(fl, -1)
                elif e.key in (pygame.K_RIGHT, pygame.K_SPACE):
                    self.cycle(fl, 1)
            elif fl:
                k = fl.key
                t, c = self.text[k], self.cur[k]
                if e.key == pygame.K_BACKSPACE:
                    if self.fresh:
                        self.text[k], self.cur[k] = "", 0
                    elif c > 0:
                        self.text[k], self.cur[k] = t[:c - 1] + t[c:], c - 1
                    self.fresh = False
                    self.recalc()
                elif e.key == pygame.K_DELETE:
                    if self.fresh:
                        self.text[k], self.cur[k] = "", 0
                    else:
                        self.text[k] = t[:c] + t[c + 1:]
                    self.fresh = False
                    self.recalc()
                elif e.key == pygame.K_LEFT:
                    self.cur[k], self.fresh = max(0, c - 1), False
                elif e.key == pygame.K_RIGHT:
                    self.cur[k], self.fresh = min(len(t), c + 1), False
                elif e.key == pygame.K_HOME:
                    self.cur[k], self.fresh = 0, False
                elif e.key == pygame.K_END:
                    self.cur[k], self.fresh = len(t), False
        return None

    def cycle(self, fl, d):
        self.choice[fl.key] = (self.choice[fl.key] + d) % len(fl.opts)
        self.recalc()
        if self.focus < len(self.c.fields) and not self.is_active(self.c.fields[self.focus]):
            self.move_focus(1)

    # ---- drawing
    def draw(self, surf):
        f = self.f
        W, H_ = surf.get_size()
        surf.fill(BG)
        pygame.draw.rect(surf, NAVY, (0, 0, W, 54))
        surf.blit(f.title.render(self.c.title, True, WHITE), (20, 11))
        hint = f.small.render("Esc = menu    Tab = next field    Ctrl+R = reset", True, (200, 212, 235))
        surf.blit(hint, (W - hint.get_width() - 20, 5))
        draw_cont_button(surf, f, W - 20, 27)

        lw = min(580, int(W * 0.46))
        left, top = 20, 70
        cache = {}
        y = top
        for k, fl in enumerate(self.c.fields):
            act = self.is_active(fl)
            col = INK if act else (160, 166, 176)
            if fl.wide:   # label on its own line, full-width input box under it
                surf.blit(f.ui.render(clip_text(f.ui, fl.label, lw), True, col), (left, y + 2))
                box = pygame.Rect(left, y + 26, lw, 30)
                rowh = 64
            else:
                lab = clip_text(f.ui, fl.label, lw - 215)
                surf.blit(f.ui.render(lab, True, col), (left, y + 5))
                box = pygame.Rect(left + lw - 205, y, 205, 30)
                rowh = 38
            cache[k] = (box, True)
            if fl.kind == "choice":
                pygame.draw.rect(surf, INPUT_FOCUS if (k == self.focus and act) else (232, 236, 244), box, border_radius=5)
                pygame.draw.rect(surf, NAVY if k == self.focus else LINE, box, 2 if k == self.focus else 1, border_radius=5)
                surf.blit(f.uib.render("<", True, NAVY), (box.x + 8, box.y + 4))
                surf.blit(f.uib.render(">", True, NAVY), (box.right - 18, box.y + 4))
                txt = clip_text(f.ui, fl.opts[self.choice[fl.key]], box.w - 44)
                ts = f.ui.render(txt, True, INK)
                surf.blit(ts, (box.centerx - ts.get_width() // 2, box.y + 5))
            else:
                focused = (k == self.focus and act)
                bgc = (235, 238, 244) if not act else (INPUT_FOCUS if focused else INPUT_BG)
                pygame.draw.rect(surf, bgc, box, border_radius=5)
                pygame.draw.rect(surf, NAVY if focused else LINE, box, 2 if focused else 1, border_radius=5)
                txt = self.text[fl.key]
                avail = box.w - 14
                tw = f.mono.size(txt)[0]
                shown, off = txt, 0
                if tw > avail:  # keep the cursor visible
                    c = self.cur[fl.key]
                    while f.mono.size(txt[off:c])[0] > avail - 4 and off < len(txt):
                        off += 1
                    shown = txt[off:]
                ts = f.mono.render(shown, True, INK if act else GREY)
                if focused and self.fresh and txt:
                    pygame.draw.rect(surf, (120, 170, 240), (box.x + 6, box.y + 4, min(ts.get_width(), avail), 22))
                surf.blit(ts, (box.x + 7, box.y + 5), area=pygame.Rect(0, 0, avail, 24))
                if focused and not self.fresh and (pygame.time.get_ticks() // 500) % 2 == 0:
                    cx = box.x + 7 + f.mono.size(txt[off:self.cur[fl.key]])[0]
                    pygame.draw.line(surf, INK, (cx, box.y + 5), (cx, box.y + 25), 2)
            y += rowh
        # hint for focused field
        if self.c.fields:
            fh = self.c.fields[self.focus].hint
            if fh:
                for ln in wrap(fh, f.smalli, lw):
                    surf.blit(f.smalli.render(ln, True, GREY), (left, y))
                    y += 18
        y += 8
        # formula + note boxes
        for title, body, font, col in (("Formulas", self.c.formula, f.monos, INK), ("Notes / slide checks", self.c.note, f.small, NOTE)):
            if not body:
                continue
            lines = wrap(body, font, lw - 24)
            lh = font.get_linesize() + 1
            bh = len(lines) * lh + 34
            if y + bh > H_ - 36:
                lines = lines[:max(1, (H_ - 36 - y - 34) // lh)]
                bh = len(lines) * lh + 34
            if bh < 50:
                continue
            pygame.draw.rect(surf, WHITE, (left, y, lw, bh), border_radius=6)
            pygame.draw.rect(surf, LINE, (left, y, lw, bh), 1, border_radius=6)
            surf.blit(f.small.render(title, True, NAVY), (left + 10, y + 6))
            for n_, ln in enumerate(lines):
                surf.blit(font.render(ln, True, col), (left + 12, y + 28 + n_ * lh))
            y += bh + 10

        # ---- results panel
        rx = left + lw + 24
        rw = W - rx - 20
        area = pygame.Rect(rx, 66, rw, H_ - 66 - 34)
        pygame.draw.rect(surf, WHITE, area, border_radius=8)
        pygame.draw.rect(surf, LINE, area, 1, border_radius=8)
        self.view_h = area.h
        prev = surf.get_clip()
        surf.set_clip(area.inflate(-2, -2))
        yy = area.y + 10 - self.scroll
        x0, x1 = area.x + 14, area.right - 24
        if self.err:
            for ln in wrap(self.err, f.h1, rw - 40):
                surf.blit(f.h1.render(ln, True, ERR), (x0, yy))
                yy += 26
        for o in self.outs:
            if o.style == "head":
                yy += 4
                pygame.draw.rect(surf, NAVY, (area.x + 6, yy, rw - 24, 26), border_radius=4)
                surf.blit(f.uib.render(o.label, True, WHITE), (x0, yy + 3))
                yy += 32
            elif o.style == "note":
                for ln in wrap(o.label, f.smalli, rw - 40):
                    surf.blit(f.smalli.render(ln, True, NOTE), (x0, yy))
                    yy += 19
                yy += 4
            else:
                key = o.style == "key"
                if key:
                    pygame.draw.rect(surf, KEY_BG, (area.x + 6, yy - 2, rw - 24, 32), border_radius=5)
                fnt = f.monob if key else f.mono
                lf = f.uib if key else f.ui
                vs = fnt.render(o.value, True, INK)
                lab = clip_text(lf, o.label, rw - 52 - vs.get_width())
                surf.blit(lf.render(lab, True, INK), (x0, yy + 3))
                surf.blit(vs, (x1 - vs.get_width(), yy + 3))
                rowh = 34 if key else 28
                if o.tv is not None:   # same answer using rounded table factors
                    tl = f.smalli.render("with table factors:  " + o.tv, True, NOTE)
                    surf.blit(tl, (x1 - tl.get_width(), yy + rowh - 4))
                    rowh += 16
                yy += rowh
        if self.table:
            hdrs, rows = self.table
            yy += 8
            cw = f.monos.size("0")[0]
            widths = []
            for c_ in range(len(hdrs)):
                m = max([len(hdrs[c_])] + [len(r[c_]) for r in rows[:800]])
                widths.append(m * cw + 18)
            tw = sum(widths)
            tx = x0
            scale_note = tw > rw - 40
            pygame.draw.rect(surf, (217, 225, 242), (area.x + 6, yy, min(tw, rw - 24), 24))
            xx = tx
            for c_, h in enumerate(hdrs):
                s_ = f.monos.render(h, True, NAVY)
                surf.blit(s_, (xx + widths[c_] - s_.get_width() - 8, yy + 3))
                xx += widths[c_]
            yy += 26
            for ri, r in enumerate(rows):
                if yy + 22 > area.y and yy < area.bottom:
                    if ri % 2:
                        pygame.draw.rect(surf, (246, 248, 252), (area.x + 6, yy, min(tw, rw - 24), 22))
                    xx = tx
                    for c_, cell in enumerate(r):
                        s_ = f.monos.render(cell, True, INK)
                        surf.blit(s_, (xx + widths[c_] - s_.get_width() - 8, yy + 2))
                        xx += widths[c_]
                yy += 22
            if scale_note:
                surf.blit(f.smalli.render("(table is wider than the window - enlarge the window)", True, GREY), (x0, yy))
                yy += 20
        self.content_h = yy + self.scroll - area.y + 10
        surf.set_clip(prev)
        self.maxs = max(0, self.content_h - area.h)
        self.scroll = min(self.scroll, self.maxs)
        self.bar.draw(surf, pygame.Rect(area.right - 14, area.y + 6, 10, area.h - 12), self.scroll, self.maxs)
        foot = f.small.render("Rates are typed in percent (8 = 8%, 6/12 = 0.5%).  First keystroke replaces the field.", True, GREY)
        surf.blit(foot, (20, H_ - 26))
        return cache


class Menu:
    def __init__(self, fonts):
        self.f = fonts
        self.q = ""
        self.sel = 0
        self.scroll = 0
        self.items = []
        self.maxs = 0
        self.bar = ScrollBar()

    def filtered(self):
        q = self.q.lower().strip()
        if not q:
            return CALCS
        out = []
        for c in CALCS:
            hay = (c.title + " " + c.desc + " " + c.kw + " " + c.cat).lower()
            if all(w in hay for w in q.split()):
                out.append(c)
        return out

    def layout(self, W):
        items = self.filtered()
        cols = max(1, min(4, (W - 40) // 290))
        cw = (W - 40 - (cols - 1) * 14) // cols
        ch = 78
        y = 150
        rects, heads = [], []
        if self.q.strip():
            for n_, c in enumerate(items):
                r = pygame.Rect(20 + (n_ % cols) * (cw + 14), y + (n_ // cols) * (ch + 12), cw, ch)
                rects.append((c, r))
            total = y + ((len(items) + cols - 1) // cols) * (ch + 12)
        else:
            for cat in CATS:
                cs = [c for c in items if c.cat == cat]
                if not cs:
                    continue
                heads.append((cat, y))
                y += 32
                for n_, c in enumerate(cs):
                    r = pygame.Rect(20 + (n_ % cols) * (cw + 14), y + (n_ // cols) * (ch + 12), cw, ch)
                    rects.append((c, r))
                y += ((len(cs) + cols - 1) // cols) * (ch + 12) + 8
            total = y
        return items, rects, heads, total, cols

    def draw(self, surf):
        f = self.f
        W, H_ = surf.get_size()
        surf.fill(BG)
        items, rects, heads, total, cols = self.layout(W)
        self.sel = max(0, min(self.sel, max(0, len(rects) - 1)))
        maxs = max(0, total - H_ + 20)
        self.maxs = maxs
        self.scroll = max(0, min(self.scroll, maxs))
        # keep selection visible
        if rects:
            r = rects[self.sel][1]
            if r.top - self.scroll < 140:
                self.scroll = max(0, r.top - 150)
            elif r.bottom - self.scroll > H_ - 10:
                self.scroll = min(maxs, r.bottom - H_ + 20)
        for cat, y in heads:
            yy = y - self.scroll
            if yy > 100:
                surf.blit(f.h1.render(cat.upper(), True, NAVY), (22, yy + 4))
                pygame.draw.line(surf, LINE, (20, yy + 28), (W - 24, yy + 28))
        mx, my = pygame.mouse.get_pos()
        hit = None
        for n_, (c, r) in enumerate(rects):
            rr = r.move(0, -self.scroll)
            if rr.bottom < 140 or rr.top > H_:
                continue
            if rr.collidepoint(mx, my) and my > 140 and pygame.mouse.get_focused() and getattr(self, "moved", False):
                self.sel = n_
            on = n_ == self.sel
            pygame.draw.rect(surf, HOVER if on else WHITE, rr, border_radius=8)
            pygame.draw.rect(surf, NAVY if on else LINE, rr, 2 if on else 1, border_radius=8)
            surf.blit(f.uib.render(clip_text(f.uib, c.title, rr.w - 20), True, NAVY), (rr.x + 10, rr.y + 8))
            for k, ln in enumerate(wrap(c.desc, f.small, rr.w - 20)[:3]):
                surf.blit(f.small.render(ln, True, GREY), (rr.x + 10, rr.y + 32 + k * 17))
        # header drawn last so scrolled cards go under it
        pygame.draw.rect(surf, NAVY, (0, 0, W, 140))
        surf.blit(f.title.render("GENG-3130  Engineering Economics Calculator", True, WHITE), (20, 12))
        surf.blit(f.small.render("Pick a question type below (mouse, or arrows + Enter).  Start typing to search, e.g. 'bond', 'gradient', 'loan'.",
                                 True, (200, 212, 235)), (22, 50))
        sb = pygame.Rect(20, 84, min(W - 40, 560), 38)
        pygame.draw.rect(surf, WHITE, sb, border_radius=6)
        txt = self.q if self.q else ""
        if txt:
            surf.blit(f.ui.render(txt, True, INK), (sb.x + 12, sb.y + 8))
        else:
            surf.blit(f.ui.render("Search...", True, (150, 156, 168)), (sb.x + 12, sb.y + 8))
        if (pygame.time.get_ticks() // 500) % 2 == 0:
            cx = sb.x + 12 + f.ui.size(txt)[0] + 2
            pygame.draw.line(surf, INK, (cx, sb.y + 8), (cx, sb.y + 30), 2)
        if not rects:
            surf.blit(f.h1.render("No match - clear the search with Backspace.", True, WHITE), (20, 170))
        self._rects = rects
        draw_cont_button(surf, f, W - 24, 90)
        # scrollbar on the right edge (sits in the margin, cards don't move)
        self.bar.draw(surf, pygame.Rect(W - 16, 148, 10, H_ - 156), self.scroll, maxs)
        return rects

    def on_event(self, e):
        s = self.bar.event(e, self.scroll, self.maxs)
        if s is not None:
            self.scroll = s
            return None
        n = len(self._rects) if hasattr(self, "_rects") else 0
        if e.type == pygame.MOUSEMOTION:
            self.moved = True
        elif e.type == pygame.MOUSEWHEEL:
            self.scroll = max(0, self.scroll - e.y * 60)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for c, r in self._rects:
                if r.move(0, -self.scroll).collidepoint(e.pos) and e.pos[1] > 140:
                    return c
        elif e.type == pygame.TEXTINPUT:
            self.q += e.text
            self.sel, self.scroll = 0, 0
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_BACKSPACE:
                self.q, self.sel, self.scroll = self.q[:-1], 0, 0
            elif e.key == pygame.K_ESCAPE:
                self.q, self.sel, self.scroll = "", 0, 0
            elif e.key in (pygame.K_RIGHT, pygame.K_DOWN, pygame.K_TAB):
                step = 1
                if e.key == pygame.K_DOWN:
                    step = max(1, min(4, (pygame.display.get_surface().get_width() - 40) // 290))
                self.sel = min(n - 1, self.sel + step) if n else 0
                self.moved = False
            elif e.key in (pygame.K_LEFT, pygame.K_UP):
                step = 1
                if e.key == pygame.K_UP:
                    step = max(1, min(4, (pygame.display.get_surface().get_width() - 40) // 290))
                self.sel = max(0, self.sel - step)
                self.moved = False
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and n:
                return self._rects[self.sel][0]
            elif e.key == pygame.K_PAGEDOWN:
                self.scroll += 400
            elif e.key == pygame.K_PAGEUP:
                self.scroll -= 400
        return None


def main():
    pygame.init()
    pygame.display.set_caption("GENG-3130 Engineering Economics Calculator")
    screen = pygame.display.set_mode((1360, 840), pygame.RESIZABLE)
    pygame.key.start_text_input()
    fonts = Fonts()
    menu = Menu(fonts)
    current = None
    screens = {}
    clock = pygame.time.Clock()
    cache = {}
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if e.type == pygame.VIDEORESIZE:
                continue
            if e.type == pygame.KEYDOWN and e.key == pygame.K_k and (e.mod & pygame.KMOD_CTRL):
                toggle_cont(screens)
                continue
            if (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and CONT_RECT[0] is not None
                    and CONT_RECT[0].collidepoint(e.pos)):
                toggle_cont(screens)
                continue
            if current is None:
                r = menu.on_event(e)
                if r is not None:
                    if r not in screens:
                        screens[r] = CalcScreen(r, fonts)
                    current = screens[r]
            else:
                if current.on_event(e, cache) == "menu":
                    current = None
        screen = pygame.display.get_surface()
        if current is None:
            menu.draw(screen)
        else:
            cache = current.draw(screen)
        pygame.display.flip()
        clock.tick(30)


if __name__ == "__main__":
    main()
