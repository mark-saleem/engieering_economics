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
  Ctrl+D               dark mode ON/OFF (also the button in the top corner)
  Ctrl+T               solve with EQUATIONS or printed TABLE factors (button in the top corner)
  Ctrl+K               continuous compounding ON/OFF (only on the Nominal -> Effective screen)
  Ctrl+R               reset this screen to its example values
  Esc                  back to the menu

Menu page: the Assignment 1-5 and Quiz 1-5 sections at the bottom have one button per question.
Clicking one opens the right calculator with the question (numbers blanked) in the notes area.

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


TBL = [False]         # True while a calculation runs with factors rounded like the printed tables
MISS = [False]        # set when a factor was asked for a rate / N that the printed tables don't contain
TABLE_MODE = [False]  # the user's choice (header button): False = equations, True = printed table factors


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


def FPz(i, n): return 1.0 if n == 0 else FP(i, n)
def PFz(i, n): return 1.0 if n == 0 else PF(i, n)


def mv(i, n):
    """Factor that moves a value n periods LATER (n > 0, F/P) or EARLIER (n < 0, P/F)."""
    return FPz(i, n) if n >= 0 else PF(i, -n)


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
        self.tv = None      # the same answer computed the OTHER way (filled in automatically)
        self.tlabel = ""    # "with table factors" or "with equations"


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
    def __init__(self, cat, title, desc, fields, fn, formula="", note="", active=None, kw="", notable=False,
                 contbtn=False):
        self.cat, self.title, self.desc = cat, title, desc
        self.fields, self.fn = fields, fn
        self.formula, self.note, self.active, self.kw = formula, note, active, kw
        self.notable = notable   # True = skip the automatic equation-vs-table second pass
        self.contbtn = contbtn   # True = show the "Continuous compounding" button on this screen only


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
    valT = sum(c * mv(i, T - t) for t, c in enumerate(cf))
    pw = sum(c * PFz(i, t) for t, c in enumerate(cf))
    r = irr(cf)
    outs = [K("Equivalent value at time T", money(valT)), K("Present worth at t = 0", money(pw)),
            O("Future worth at t = %d (last flow)" % n, money(pw * FPz(i, n))),
            O("Equivalent uniform amount over t=1..%d" % n, money(pw * AP(i, n)) if n > 0 else "n/a"),
            O("IRR of this stream (PW = 0)", pc(r) if r is not None else "none (needs + and - flows)")]
    rows = [[str(t), "%.2f" % c, "%.5f" % mv(i, T - t), "%.2f" % (c * mv(i, T - t))] for t, c in enumerate(cf)]
    return outs, (["t", "Cash flow", "(F/P or P/F) to T", "Value at T"], rows), CFDiagram(cf, mark=int(round(T)))


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
    pw = at_k * PFz(i, k)
    vT = pw * FPz(i, T)
    outs = [O("(A/G, i, N)", fac(ag)), O("G x (A/G, i, N)", money(G * ag)),
            K("Equivalent uniform series  A_total", money(At)),
            O("Value at t = k  (one period before 1st flow)", money(at_k)),
            K("Present worth at t = 0  (series only)", money(pw)),
            O("Future worth at t = k+N  (last flow)", money(At * FA(i, n))),
            O("Value of series at time T", money(vT)),
            K("Value at T including the t=0 flow", money(vT + C0 * FPz(i, T))),
            O("Present worth at t=0 including t=0 flow", money(pw + C0))]
    if abs(v["div"] - 1) > 1e-12 and v["div"] != 0:
        outs.append(K("Present worth per share / ticket  (PW / %s)" % num(v["div"], 0), money((pw + C0) / v["div"])))
    return outs, None


reg("Gradients", "Arithmetic Gradient  A' +/- G", "A_total, PW, FW, value at time T (2023 midterm style)",
    [Field("i", "Interest rate per period, i (%)", "3", "pct"),
     Field("N", "Number of flows in the series, N", "16", hint="Count every flow in the series, including the first"),
     Field("A1", "First flow of the series, A' ($)", "10000"), Field("G", "Gradient, G ($) (positive)", "500"),
     Field("dir", "Direction", opts=["Decreasing", "Increasing"], kind="choice"),
     Field("k", "Delay k (first flow at t = k+1)", "1", hint="Series from t=1 -> k=0; from t=2 -> k=1; from t=3 -> k=2"),
     Field("T", "Time T for equivalent value", "20", hint="T = 0 gives present worth"),
     Field("C0", "Extra flow at t=0 ($) (cost negative)", "-8000"),
     Field("div", "Divide PW by (tickets, shares...) (1 = skip)", "1")],
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


def a_eff(v):
    return {"r"} if v.get("_cont") else {"r", "m"}


def c_eff(v):
    r, m = v["r"], v["m"]
    cols = [0.06, 0.10, 0.12, 0.20, 0.24, 0.25]
    rows = []
    for mm in (1, 2, 4, 12, 52, 365):
        rows.append([str(mm)] + ["%.4f%%" % (((1 + c / mm) ** mm - 1) * 100) for c in cols])
    rows.append(["cont."] + ["%.4f%%" % ((math.exp(c) - 1) * 100) for c in cols])
    tab = (["m \\ r"] + ["%d%%" % round(c * 100) for c in cols], rows)
    if v.get("_cont"):
        return [N("Continuous compounding is ON  (m -> infinity, so m is not used)."),
                K("Effective annual rate  i_e = e^r - 1", pc(math.exp(r) - 1)),
                O("e^r", "%.6f" % math.exp(r)),
                O("For comparison: daily compounding (m = 365)", pc((1 + r / 365) ** 365 - 1)),
                N("Table: effective annual rate for nominal rate (columns) and m per year (rows).")], tab
    return [O("Rate per compounding period  i_s = r/m", pc(r / m, 6)),
            K("Effective annual rate  i_e = (1+r/m)^m - 1", pc((1 + r / m) ** m - 1)),
            O("For comparison: continuous compounding  i_e = e^r - 1", pc(math.exp(r) - 1)),
            N("Table: effective annual rate for nominal rate (columns) and m per year (rows).")], tab


reg("Capital & Rates", "Nominal -> Effective Rate", "i_e from r and m; has a continuous-compounding switch (top corner)",
    [Field("r", "Nominal annual rate, r (%)", "24", "pct"), Field("m", "Compounding periods per year, m", "365")],
    c_eff, "i_e = (1 + r/m)^m - 1          continuous: i_e = e^r - 1",
    "Slide checks: 24% daily -> 27.11% | 12% continuous -> 12.75% | 25% semi-annual 26.56%, quarterly 27.44%, continuous 28.40%. "
    "Click 'Continuous compounding' in the top corner (or Ctrl+K) to switch the answer to e^r - 1.",
    active=a_eff, notable=True, contbtn=True, kw="ear effective apr continuous")


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
    P, r, m, Np, A = v["P"], v["r"], v["m"], int(round(v["Np"])), v["A"]
    if m <= 0:
        raise InputError("Payments per year must be above 0.")
    i = r / m
    outs = [O("Rate per payment period  i = r/m", pc(i, 5))]
    if Np > 0:
        outs += [O("(A/P, i, %d)" % Np, fac(AP(i, Np))),
                 K("Planned payment  A = P(A/P,i,N)", money(P * AP(i, Np)))]
    if A <= 0:
        raise InputError("Enter the payment you will actually make each period.")
    if A <= P * i + 1e-9:
        raise InputError("That payment (%s) does not even cover the interest (P x i = %s), so the debt is never paid off."
                         % (money(A), money(P * i)))
    target = P / A
    n_exact = -math.log(1 - P * i / A) / math.log(1 + i)
    # linear interpolation between the two table rows (P/A,i,N) that bracket P/A
    n0 = int(math.floor(n_exact))
    f0, f1 = (PA(i, n0) if n0 >= 1 else 0.0), PA(i, n0 + 1)
    n_interp = n0 + (target - f0) / (f1 - f0) if f1 != f0 else n_exact
    n_use = n_interp if TBL[0] else n_exact
    full = int(math.floor(n_use + 1e-12))
    pay = full + (0 if abs(n_use - full) < 1e-9 else 1)
    last = (P - A * (PA(i, full) if full >= 1 else 0.0)) * FPz(i, full + 1) if pay > full else A
    outs += [O("Target factor  (P/A, i, N) = P / A", "%.5f" % target),
             O("N by the formula  -ln(1 - P i/A) / ln(1+i)", num(n_exact)),
             O("N by linear interpolation in the (P/A) table", num(n_interp)),
             K("Number of payments needed  (round up)", "%d" % pay),
             O("... in years", num(pay / m, 2)),
             O("Full-size payments", "%d" % full),
             K("Last payment (period %d)" % pay, money(last))]
    return outs, None


reg("Loans & Bonds", "Loan Payment & Payoff Time", "Planned payment, how many payments at a bigger payment, and the last one",
    [Field("P", "Loan / investment amount, P ($)", "800000"),
     Field("r", "Nominal annual interest rate, r (%)", "5", "pct"),
     Field("m", "Payments (and compounding) per year, m", "1", hint="Annual payments -> 1. Monthly savings, 6% compounded monthly -> r = 6, m = 12"),
     Field("Np", "Planned number of payments (0 = skip)", "15"),
     Field("A", "Payment you actually make each period, A ($)", "100000")],
    c_payoff,
    "A = P(A/P,i,N)      (P/A, i, N) = P / A  ->  N = -ln(1 - P i/A)/ln(1+i)\n"
    "Last = [P - A(P/A,i,N_full)](F/P,i,N_full+1)      i = r/m",
    "Slide check (2023 Q6): 300,000 at 5%, 50,000/yr (planned 0) -> 8 payments, last payment 15,779.21 on the slide (rounded table factors); "
    "exact math gives 15,781.19. In TABLE mode, N comes from linear interpolation between the two (P/A) table rows.",
    kw="payoff payback recoup last payment loan solar panels")


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
            K("Charge in the NEXT year (n+1)  BV(n) x d", money(bvd * d)),
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
    if v["contx"].startswith("Yes"):
        rs += [math.exp(r) - 1 for r in base]
        notes.append("Also tried continuous compounding: i = e^r - 1 for each rate.")
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
     Field("split", "Also try nominal splits (r/12, r/4...)", opts=["No", "Yes"], kind="choice"),
     Field("contx", "Also try continuous compounding (e^r - 1)", opts=["No", "Yes"], kind="choice")],
    c_finder,
    "Tries factors, P/F/A/G conversions, PW / AW with salvage, capital recovery, perpetuities, geometric gradients, "
    "non-standard annuities, effective / nominal / continuous rates and i from P and F.",
    "Each match shows the EQUATION value and the TABLE value (factors rounded to 5 significant digits, like Appendix A). "
    "Table values only appear when the rate is a table rate (multiples of 0.25%) and N is 1 to 100. "
    "Defaults = Margaret's project: -2,164 is the PW and 25,094 is 5,000(P/A,15%,10).",
    notable=True, kw="answer finder multiple choice check solver which options within 2 percent")



# =====================================================================
#  CALCULATORS ADDED FOR THE ASSIGNMENT AND QUIZ QUESTIONS
# =====================================================================
class CFDiagram:
    """Cash flow diagram: up arrow = receive, down arrow = pay; optional rate of each period
    and a marked time T (dashed green line)."""
    HEIGHT = 262

    def __init__(self, cf, rates=None, mark=None, upto=0):
        self.cf = list(cf)
        self.rates = list(rates or [])
        self.mark = mark
        self.n = max(len(self.cf) - 1, int(upto or 0), int(mark or 0), len(self.rates), 1)

    def draw(self, surf, f, rect):
        pygame.draw.rect(surf, PANEL2, rect, border_radius=6)
        pygame.draw.rect(surf, LINE, rect, 1, border_radius=6)
        legend = "Up arrow = receive, down arrow = pay"
        if self.rates:
            legend += "      bottom row = rate of each period"
        surf.blit(f.smalli.render(clip_text(f.smalli, legend, rect.w - 16), True, GREY), (rect.x + 8, rect.y + 5))
        n = self.n
        x0, x1 = rect.x + 34, rect.right - 26
        step = (x1 - x0) / float(n)
        top, bot = rect.y + 44, rect.bottom - 72
        pos = max([c for c in self.cf if c > 0] + [0.0])
        neg = -min([c for c in self.cf if c < 0] + [0.0])
        scale = (bot - top) / (pos + neg) if pos + neg > 0 else 1.0
        axis = int(top + pos * scale)
        pygame.draw.line(surf, INK, (x0 - 12, axis), (x1 + 14, axis), 2)
        pygame.draw.polygon(surf, INK, [(x1 + 18, axis), (x1 + 8, axis - 4), (x1 + 8, axis + 4)])
        skip = max(1, int(math.ceil(24.0 / step)))
        for t in range(0, n + 1):
            x = int(x0 + t * step)
            pygame.draw.line(surf, INK, (x, axis - 4), (x, axis + 4), 1)
            if t % skip == 0:
                s = f.small.render(str(t), True, INK)
                surf.blit(s, (x - s.get_width() // 2, rect.bottom - 48))
        if self.mark is not None:
            x = int(x0 + self.mark * step)
            y = rect.y + 24
            while y < rect.bottom - 52:
                pygame.draw.line(surf, POS, (x, y), (x, min(y + 6, rect.bottom - 52)), 2)
                y += 11
            s = f.small.render("T = %d" % self.mark, True, POS)
            surf.blit(s, (min(x + 4, rect.right - s.get_width() - 4), rect.y + 22))
        wide = max([f.small.size(format(abs(c), ",.0f") + ("-" if c < 0 else ""))[0] for c in self.cf] + [10]) + 6
        for t, c in enumerate(self.cf):
            if c == 0:
                continue
            x = int(x0 + t * step)
            ln = max(16, abs(c) * scale)
            col = POS if c > 0 else NEG
            tip = axis - ln if c > 0 else axis + ln
            pygame.draw.line(surf, col, (x, axis), (x, int(tip)), 3)
            if c > 0:
                pts = [(x, int(tip)), (x - 6, int(tip) + 11), (x + 6, int(tip) + 11)]
            else:
                pts = [(x, int(tip)), (x - 6, int(tip) - 11), (x + 6, int(tip) - 11)]
            pygame.draw.polygon(surf, col, pts)
            if step >= wide:
                s = f.small.render(("-" if c < 0 else "") + format(abs(c), ",.0f"), True, col)
                surf.blit(s, (x - s.get_width() // 2, int(tip) - 19 if c > 0 else int(tip) + 4))
        if self.rates and step >= 30:
            for k, r in enumerate(self.rates[:n], 1):
                s = f.small.render(("%g" % round(r * 100, 4)) + "%", True, ACCENT)
                surf.blit(s, (int(x0 + (k - 0.5) * step) - s.get_width() // 2, rect.bottom - 24))


# ---------- nominal rate: future / present value ----------
def a_nomfv(v):
    return {"F": {"P", "r", "m", "yrs"}, "P": {"F", "r", "m", "yrs"}}[v["solve"][0]]


def c_nomfv(v):
    P, F, r, m, yrs = v["P"], v["F"], v["r"], v["m"], v["yrs"]
    if m <= 0 or yrs < 0:
        raise InputError("Compounding periods per year must be above 0 and the time cannot be negative.")
    i, n = r / m, m * yrs
    ear = (1 + i) ** m - 1
    outs = [O("Nominal annual rate, r", pc(r, 4)), O("Rate per compounding period  i = r/m", pc(i, 5)),
            O("Number of periods  N = m x years", "%g" % n)]
    if v["solve"][0] == "F":
        f = FP(i, n)
        outs += [O("(F/P, i, N)", "%.5f" % f), K("Future amount  F = P(F/P,i,N) = P(1+i)^N", money(P * f)),
                 O("Interest earned  F - P", money(P * f - P))]
    else:
        f = PF(i, n)
        outs += [O("(P/F, i, N)", "%.5f" % f), K("Present amount  P = F(P/F,i,N) = F/(1+i)^N", money(F * f))]
    outs += [H("Rates"), K("Nominal annual rate", pc(r, 4)), K("Effective annual rate  i_e = (1+r/m)^m - 1", pc(ear, 4))]
    return outs, None


reg("Basics", "Compound Interest with Nominal Rate", "F or P from a nominal rate, compounding frequency and years (also gives the EAR)",
    [Field("solve", "Solve for", opts=["F (future value)", "P (present value)"], kind="choice"),
     Field("P", "Present amount, P ($)", "100000"), Field("F", "Future amount, F ($)", "1000000"),
     Field("r", "Nominal annual rate, r (%)", "6", "pct"),
     Field("m", "Compounding periods per year, m", "4", hint="Monthly 12, quarterly 4, semi-annual 2, weekly 52, daily 365"),
     Field("yrs", "Time, in years", "5")],
    c_nomfv, "i = r/m      N = m x years      F = P(1+i)^N = P(F/P,i,N)      P = F/(1+i)^N = F(P/F,i,N)      i_e = (1+r/m)^m - 1",
    "Check: $100,000 at 6% compounded quarterly for 5 years -> F = 134,685.50, EAR 6.1364%.  "
    "TABLE mode needs i and N in the printed table (e.g. 1.5% x 20 quarters); otherwise the exact formula is used.",
    active=a_nomfv, kw="gic deposit accumulate discounting nominal effective")


# ---------- compare options by EAR ----------
def c_funds(v):
    rs, ms, P, yrs = v["rs"], v["ms"], v["P"], v["yrs"]
    if not rs or len(rs) != len(ms):
        raise InputError("Type one compounding number for every nominal rate (you have %d rates and %d m values)." % (len(rs), len(ms)))
    if any(m <= 0 for m in ms):
        raise InputError("Every compounding number m must be above 0.")
    ears = [(1 + r / m) ** m - 1 for r, m in zip(rs, ms)]
    hi = v["goal"].startswith("Highest")
    pick = max if hi else min
    best = pick(range(len(ears)), key=lambda k: ears[k])
    outs = [H("Effective annual rate (EAR) of each option")]
    for k, (r, m, e) in enumerate(zip(rs, ms, ears)):
        outs.append((K if k == best else O)("Option %d:  r = %s, m = %g" % (k + 1, pc(r, 3), m), pc(e, 3)))
    i, n = rs[best] / ms[best], ms[best] * yrs
    f = FP(i, n)
    outs += [H("Best option"),
             K("%s EAR  ->  Option %d" % ("Highest" if hi else "Lowest", best + 1), pc(ears[best], 3)),
             O("(F/P, i, N)   i = r/m = %s,  N = %g" % (pc(i, 5), n), "%.5f" % f),
             K("%s after %g years at the best option" % ("Value of the investment" if hi else "Amount owed", yrs), money(P * f))]
    rows = [[str(k + 1), pc(r, 3), "%g" % m, pc(r / m, 5), pc(e, 3), pc(e, 4), "<- best" if k == best else ""]
            for k, (r, m, e) in enumerate(zip(rs, ms, ears))]
    return outs, (["Option", "r", "m", "r/m", "EAR 3dp", "EAR 4dp", ""], rows)


reg("Basics", "Compare Nominal Rates (EAR)", "EAR of 2 or more offers (funds, banks) -> best one, then the value after N years",
    [Field("rs", "Nominal annual rates of the options (% list)", "8.5, 8.2, 8.7", "plist", wide=True),
     Field("ms", "Compounding periods per year for each (list)", "2, 365, 4", "list", wide=True,
           hint="Same order as the rates: semi-annual 2, quarterly 4, monthly 12, daily 365"),
     Field("goal", "Best option is the", opts=["Highest EAR (investing)", "Lowest EAR (borrowing)"], kind="choice"),
     Field("P", "Amount invested / borrowed, P ($)", "500000"), Field("yrs", "Years", "3")],
    c_funds, "EAR = (1 + r/m)^m - 1          value after N years = P(1 + r/m)^(m x N)",
    "Check: 8.5% semi-annual 8.681%, 8.2% daily 8.545%, 8.7% quarterly 8.988% (best); $500,000 for 3 years at the best one -> $647,300.23.",
    kw="ear funds banks loan compare best mutual")


# ---------- cash flow diagram ----------
def c_cfd(v):
    cf, upto = v["cf"], int(round(v["upto"]))
    if not cf:
        raise InputError("Enter the cash flows at t = 0, 1, 2, ...")
    rec = sum(c for c in cf if c > 0)
    pay = -sum(c for c in cf if c < 0)
    outs = [K("Net sum of the flows (no interest)", money(rec - pay)),
            O("Total received (up arrows)", money(rec)), O("Total paid (down arrows)", money(pay)),
            O("Periods drawn", "%d" % max(len(cf) - 1, upto)),
            N("Up arrow = you receive money, down arrow = you pay money. Copy the picture onto your answer sheet.")]
    rows = [[str(t), "Receive" if c > 0 else ("Pay" if c < 0 else "-"), "%.2f" % abs(c)] for t, c in enumerate(cf)]
    return outs, (["t", "Arrow", "Amount"], rows), CFDiagram(cf, upto=upto)


reg("Basics", "Cash Flow Diagram", "Draws the arrows for a list of receipts and payments (t = 0, 1, 2, ...)",
    [Field("cf", "Cash flows at t=0,1,2,... (list)", "1000, -4000, -4000, 5000, 5000, 5000, -6000", "list", wide=True,
           hint="Receive = positive, pay = negative, no flow = 0"),
     Field("upto", "Show the axis up to period (0 = auto)", "7")],
    c_cfd, "", "Check (Tutorial 1 #4): receive 1,000 at t=0, pay 4,000 at t=1 and 2, receive 5,000 at t=3, 4, 5, pay 6,000 at t=6.",
    notable=True, kw="draw diagram arrows prepare")


# ---------- equal deposits to reach a goal ----------
def c_goal(v):
    F, P0, i, n = v["F"], v["P0"], v["i"], v["N"]
    fp, af = FP(i, n), AF(i, n)
    grow = P0 * fp
    need = F - grow
    return [O("(F/P, i, N)", fac(fp)), O("Today's deposit grows to  P(F/P,i,N)", money(grow)),
            O("Still needed from the deposits  F - P(F/P,i,N)", money(need)), O("(A/F, i, N)", fac(af)),
            K("Equal deposit each period  A = [F - P(F/P,i,N)](A/F,i,N)", money(need * af)),
            O("Check by present worth  [F(P/F,i,N) - P](A/P,i,N)", money((F * PF(i, n) - P0) * AP(i, n)))], None


reg("Basics", "Equal Deposits to Reach a Goal", "Goal in N years, some money today, equal end-of-period deposits: find the deposit",
    [Field("F", "Goal amount, F ($)", "25000"), Field("P0", "Amount deposited today, P ($)", "10000"),
     Field("i", "Interest rate per period, i (%)", "3", "pct"), Field("N", "Number of equal deposits, N", "5")],
    c_goal, "A = [F - P(F/P,i,N)] (A/F,i,N)      (the first deposit is at the END of period 1; the last is at time N)",
    "Check (Tutorial 3 #1): $25,000 goal, $10,000 today, 3%, 5 years -> about $2,525 per year.", kw="savings trip fund")


# ---------- annuity segments ----------
def c_segments(v):
    i, amts, ss, es, Ts = v["i"], v["amts"], v["ss"], v["es"], v["Ts"]
    if not amts or not (len(amts) == len(ss) == len(es)):
        raise InputError("Type the same number of amounts, first periods and last periods (you have %d / %d / %d)."
                         % (len(amts), len(ss), len(es)))
    segs = []
    for A, s, e in zip(amts, ss, es):
        s, e = int(round(s)), int(round(e))
        if s < 1 or e < s:
            raise InputError("Each segment needs  1 <= first period <= last period.")
        segs.append((A, s, e))
    rows, pw0 = [], 0.0
    for idx, (A, s, e) in enumerate(segs):
        n = e - s + 1
        at = A * PA(i, n)
        p0 = at * PFz(i, s - 1)
        pw0 += p0
        rows.append([str(idx + 1), "%.2f" % A, str(s), str(e), str(n), "%.5f" % PA(i, n), "%.2f" % at, "%.2f" % p0])
    outs = [H("Present worth"), K("PW at t = 0 (all segments)", money(pw0))]
    if Ts:
        outs.append(H("Value at the times you asked for"))
    for T in Ts:
        T = int(round(T))
        tot = sum(A * PA(i, e - s + 1) * mv(i, T - (s - 1)) for A, s, e in segs)
        outs.append(K("Net sum at time T = %d" % T, money(tot)))
    outs.append(N("Each segment: A(P/A,i,n) is its value ONE period before its first payment (time s-1); "
                  "then it is moved to time T with (F/P) or (P/F)."))
    return outs, (["Seg", "Amount", "From", "To", "n", "(P/A,i,n)", "Value at s-1", "PW at t=0"], rows)


reg("Basics", "Annuity Segments, Value at Time T", "Several level payment blocks (e.g. 50k for years 1-5, 70k for 6-10): PW and value at any time",
    [Field("i", "Interest rate per period, i (%)", "5", "pct"),
     Field("amts", "Payment of each block (list)", "10000, 20000", "list", wide=True),
     Field("ss", "First payment period of each block (list)", "6, 31", "list", wide=True),
     Field("es", "Last payment period of each block (list)", "20, 45", "list", wide=True),
     Field("Ts", "Times T where you want the value (list)", "2, 55", "list", wide=True, hint="T = 0 is the present worth")],
    c_segments, "Block value at s-1 = A(P/A,i,n),  n = last - first + 1      value at T = that x (F/P,i,T-(s-1))  or  (P/F,i,(s-1)-T)",
    "Checks: Tutorial 3 #2 -> 50,000 (yrs 1-5) + 70,000 (yrs 6-10) at 5% -> PW 453,931.91 (453,935.52 with tables).  "
    "Quiz 3 Q1 -> P2 = 142,623.28 with TABLES (142,619.08 exact); F55 = 1,893,291 with TABLES (answer key 1,893,298.30).", kw="deferred annuity two stage phonto net sum")


# ---------- net sum with varying rates ----------
def c_netsum(v):
    cf, rates, T = v["cf"], v["rates"], int(round(v["T"]))
    n = len(cf) - 1
    if n < 1:
        raise InputError("Enter at least two cash flows (t = 0 and t = 1).")
    if T < 0:
        raise InputError("T cannot be negative.")
    need = max(n, T)
    if len(rates) == 1:
        rates = rates * need
    if len(rates) < need:
        raise InputError("Type an interest rate for each of the %d periods (you typed %d). One rate alone is used for all periods."
                         % (need, len(rates)))
    tot, pw0, rows = 0.0, 0.0, []
    for t, c in enumerate(cf):
        f = 1.0
        if t <= T:
            for k in range(t, T):
                f *= 1 + rates[k]
        else:
            for k in range(T, t):
                f /= 1 + rates[k]
        d0 = 1.0
        for k in range(0, t):
            d0 /= 1 + rates[k]
        tot += c * f
        pw0 += c * d0
        if c != 0:
            rows.append([str(t), "%.2f" % c, "%.5f" % f, "%.2f" % (c * f)])
    outs = [K("Net sum at time T = %d" % T, money(tot)), O("Present worth at t = 0 (same rates)", money(pw0)),
            N("Flows before T are compounded forward through each period's own rate; flows after T are discounted back with the "
              "rates of the periods between T and the flow.")]
    return outs, (["t", "Cash flow", "Factor to T", "Value at T"], rows), CFDiagram(cf, rates[:need], mark=T)


reg("Basics", "Net Sum at Time T (varying rates)", "Cash flow diagram where every period has its own interest rate",
    [Field("cf", "Cash flows at t=0,1,2,... (list)", "-3000, 0, 0, 5000, 0, 0, 0, -3000, 6000", "list", wide=True,
           hint="Put 0 for periods with no arrow"),
     Field("rates", "Interest rate of period 1,2,3,... (% list)", "3, 3, 3, 7, 7, 7, 8, 8", "plist", wide=True,
           hint="Period k is between time k-1 and time k. Typing one rate uses it for every period."),
     Field("T", "Find the net sum at time T", "6")],
    c_netsum, "Value at T = sum of CF(t) x product of (1 + i_k) for t < T      and      CF(t) / product of (1 + i_k) for t > T",
    "Check (Quiz 2 Q2): defaults -> F6 = $4,475.56 (matches the quiz key).", notable=True, kw="various period interest rates cash flow diagram f6 f5")


# ---------- deposits to fund later withdrawals ----------
def c_fund(v):
    i, ds, de, W, ws, we = v["i"], int(round(v["ds"])), int(round(v["de"])), v["W"], int(round(v["ws"])), int(round(v["we"]))
    if ds < 1 or de < ds or we < ws or ws < 1:
        raise InputError("Each 'last period' must not be before its 'first period', and periods start at 1.")
    nd, nw = de - ds + 1, we - ws + 1
    pwW = W * PA(i, nw) * PFz(i, ws - 1)
    dep = PA(i, nd) * PFz(i, ds - 1)
    A = pwW / dep
    return [O("Withdrawals / deposits", "%d / %d" % (nw, nd)),
            O("(P/A, i, %d)  withdrawals" % nw, fac(PA(i, nw))), O("(P/F, i, %d)" % (ws - 1), fac(PFz(i, ws - 1))),
            O("PW of the withdrawals at t=0  W(P/A)(P/F)", money(pwW)),
            O("(P/A, i, %d)  deposits" % nd, fac(PA(i, nd))),
            K("Deposit each period  A = PW(withdrawals) / [(P/A,i,N_d)(P/F,i,s_d-1)]", money(A)),
            O("Balance right after the last deposit  A(F/A,i,N_d)", money(A * FA(i, nd)))], None


reg("Basics", "Deposits to Fund Later Withdrawals", "Equal deposits now, equal withdrawals later: find the deposit (education fund)",
    [Field("i", "Interest rate per period, i (%)", "5", "pct"),
     Field("ds", "First deposit at period", "1"), Field("de", "Last deposit at period", "10"),
     Field("W", "Withdrawal each period ($)", "30000"),
     Field("ws", "First withdrawal at period", "18"), Field("we", "Last withdrawal at period", "21")],
    c_fund, "PW(deposits) = PW(withdrawals):    A(P/A,i,N_d)(P/F,i,s_d-1) = W(P/A,i,N_w)(P/F,i,s_w-1)",
    "Check (Tutorial 3 #5): 30,000 at years 18-21, deposits years 1-10, 5% -> A is about $6,011.", kw="education fund john ella")


# ---------- capitalized cost and toll ----------
def c_capcost(v):
    C, M, i, n = v["C"], v["M"], v["i"], v["N"]
    cap = M / i
    pcost = C + cap
    toll = pcost * AP(i, n)
    return [O("Maintenance forever  A / i", money(cap)), K("Present cost  (construction + maintenance forever)", money(pcost)),
            O("(A/P, i, N)", fac(AP(i, n))), K("Toll each year to recover it in N years  A = PC(A/P,i,N)", money(toll)),
            O("Total tolls over N years (no interest)", money(toll * n))], None


reg("Capital & Rates", "Capitalized Cost & Toll Recovery", "Construction + endless maintenance = present cost, then the yearly toll to recover it",
    [Field("C", "Construction cost today, ($)", "100000000", hint="$100 million = 100000000  (or type 100*1000000)"),
     Field("M", "Maintenance every year, forever ($)", "800000"),
     Field("i", "Interest rate per year, i (%)", "5", "pct"), Field("N", "Years to recover the cost", "15")],
    c_capcost, "Present cost = C + M/i          Toll = Present cost x (A/P,i,N)",
    "Check (Tutorial 5 #2): $100M + $0.8M/yr at 5% -> present cost $116,000,000; toll over 15 years about $11.18 million per year.",
    kw="bypass toronto perpetuity")


# ---------- DBD rate matching SLD ----------
def c_dbmatch(v):
    P, S, N_, n = v["P"], v["S"], v["N"], v["n"]
    if P <= 0 or N_ <= 0 or n <= 0:
        raise InputError("Cost, life and the year must all be above 0.")
    sl = (P - S) / N_
    bv = P - n * sl
    if bv <= 0:
        raise InputError("The straight-line book value is zero or negative, so no declining-balance rate can match it.")
    d = 1 - (bv / P) ** (1.0 / n)
    return [O("SLD charge per year  (P - S)/N", money(sl)),
            K("SLD book value at end of year n   P - n(P-S)/N", money(bv)),
            K("DBD rate with the same book value   d = 1 - (BV/P)^(1/n)", pc(d, 4)),
            O("d as a decimal", "%.6f" % d), O("Check: P(1-d)^n", money(P * (1 - d) ** n))], None


reg("Other", "DBD Rate that Matches SLD", "Book value by straight-line, then the declining-balance rate that gives the same book value",
    [Field("P", "Purchase price, P ($)", "600000"), Field("S", "Salvage value, S ($)", "100000"),
     Field("N", "Useful life, N (years)", "10"), Field("n", "Year n where the book values match", "6")],
    c_dbmatch, "SLD: BV(n) = P - n(P-S)/N          DBD: BV(n) = P(1-d)^n  ->  d = 1 - (BV(n)/P)^(1/n)",
    "Check (Quiz 2 Q1): 600,000 / 100,000 / 10 yrs / year 6 -> BV 300,000 and d = 10.9101%.", notable=True, kw="windsor tool press")


# ---------- account with several kinds of flows ----------
def c_account(v):
    r, m, n, P0, A, B, k, F1, G = (v["r"], v["m"], int(round(v["N"])), v["P0"], v["A"], v["B"],
                                   int(round(v["k"])), v["F1"], v["G"])
    if m <= 0 or n < 1 or k < 1 or n % k:
        raise InputError("N must be a whole multiple of k (extra deposits every k periods, the last one at time N).")
    i = r / m
    fa = FA(i, n)
    p0 = P0 * FP(i, n)
    a_ = A * fa
    aeq = B * AF(i, k)
    b_ = aeq * fa
    f_ = F1 * fa
    ag = AG(i, n)
    g_ = G * ag * fa
    return [O("Rate per period  i = r/m", pc(i, 5)), O("(F/A, i, N)", fac(fa)),
            H("Money going in"),
            O("Starting balance  P(F/P,i,N)", money(p0)), O("Regular deposits  A(F/A,i,N)", money(a_)),
            O("Extra deposit paid every k periods as an equal amount  B(A/F,i,k)", money(aeq)),
            O("Extra deposits  B(A/F,i,k)(F/A,i,N)", money(b_)),
            H("Money going out (fees)"),
            O("Fee of the first period  F1 (F/A,i,N)", money(f_)),
            O("(A/G, i, N)", fac(ag)), O("Fee increase  G(A/G,i,N)(F/A,i,N)", money(g_)),
            H("Balance"), K("Balance at the end of period N", money(p0 + a_ + b_ - f_ - g_))], None


reg("Other", "Account: Deposits, Extras and Fees", "Start balance + monthly deposits + extra deposit every k months - growing monthly fee",
    [Field("r", "Nominal annual rate, r (%)", "6", "pct"), Field("m", "Compounding periods per year, m", "12"),
     Field("N", "Number of periods in total, N", "60"), Field("P0", "Starting balance at t=0 ($)", "3000"),
     Field("A", "Regular deposit every period ($)", "200"),
     Field("B", "Extra deposit amount ($)", "3000"), Field("k", "Extra deposit every k periods (last at N)", "6"),
     Field("F1", "Fee in period 1 ($)", "10"), Field("G", "Fee goes up by G each period ($)", "1")],
    c_account,
    "F = P(F/P,i,N) + A(F/A,i,N) + B(A/F,i,k)(F/A,i,N) - F1(F/A,i,N) - G(A/G,i,N)(F/A,i,N)\n"
    "B every k periods = B(A/F,i,k) every period.   Fee F1, F1+G, F1+2G ... = F1 + G(A/G,i,N)  as an equal yearly amount.",
    "Check (Tutorial 5 #5): defaults -> $49,800.34 (checked month by month; $49,801.45 with tables).", kw="kobe monthly management fee gradient")


CATS = ["Answer Finder", "Basics", "Gradients", "Capital & Rates", "Loans & Bonds", "Comparison", "Other"]

# =====================================================================
#  ASSIGNMENT AND QUIZ QUESTIONS  (one button each, at the bottom of the menu)
#  The question text keeps the wording but every number is a blank:
#     _____$  = dollars     __%  = percent     _  = years / months / days / count
#  The input boxes start with the ORIGINAL numbers so you can see the known answer,
#  then type the new numbers from your own question over them.
# =====================================================================
GROUPS_A = ["Assignment 1", "Assignment 2", "Assignment 3", "Assignment 4", "Assignment 5"]
GROUPS_Q = ["Quiz 1", "Quiz 2", "Quiz 3", "Quiz 4", "Quiz 5"]


class Question:
    def __init__(self, group, label, calc, text, preset=None, tip="", check=""):
        self.group, self.label, self.calc_title = group, label, calc
        self.text, self.preset, self.tip, self.check = text, preset or {}, tip, check
        hits = [c for c in CALCS if c.title.startswith(calc)]
        if not hits:
            raise RuntimeError("question %s %s: no calculator called %r" % (group, label, calc))
        self.calc = hits[0]


QUESTIONS = []


def Q(group, label, calc, text, preset=None, tip="", check=""):
    QUESTIONS.append(Question(group, label, calc, text.strip("\n"), preset, tip, check))


# ---------------------------------------------------------------- Assignment 1
Q("Assignment 1", "Problem 1", "Compound Interest with Nominal Rate",
  """For a personal investment of _____$ invested in a GIC (Guaranteed Investment Certificate) for _ years at __% interest rate per year (compounded ________), what do you expect to collect (principal plus interest) at the end of the _-th year (first day of the _th year)? Also, identify the Nominal Interest rate per year and the Effective Interest rate per year.
   Nominal Annual Interest Rate = ?
   Effective Annual Interest Rate = ?""",
  {"solve": "F", "P": "100000", "r": "6", "m": "4", "yrs": "5"})

Q("Assignment 1", "Problem 2", "Compare Nominal Rates (EAR)",
  """You are a financial analyst for a mid-sized engineering firm. The company has a surplus of _____$ in its contingency fund and wants to invest it for _ years in a low-risk portfolio. The CFO has narrowed the options down to three different mutual funds, each offering a different nominal return and compounding frequency:
  - Alpha Growth Fund: nominal return of __% per year, compounded ________.
  - Beta Income Fund: nominal return of __% per year, compounded ________ (use a 365-day year).
  - Gamma Bond Fund: nominal return of __% per year, compounded ________.
Part A: For each of the three funds, calculate the Effective Annual Rate (EAR). Show your calculations and round your final EAR values to 3 decimal places (e.g., 8.743%).
Part B: Based on the EARs you calculated, which fund provides the highest effective return?
Part C: Calculate the total value of the _____$ investment after exactly _ years if the company invests in the fund you selected in Part B. Assume that the nominal rate and compounding frequency remain constant over the _ years, and that no additional deposits or withdrawals are made.""",
  {"rs": "8.5, 8.2, 8.7", "ms": "2, 365, 4", "goal": "Highest", "P": "500000", "yrs": "3"},
  tip="Option 1 = Alpha, Option 2 = Beta, Option 3 = Gamma (same order as the lists).")

Q("Assignment 1", "Problem 3", "Compound Interest with Nominal Rate",
  """How much should you invest today at __% interest (compounded ________) to accumulate _____$ in _ years? Perform calculations using the discounting formula.
   Discounting Formula:  P = F / (1+i)^N""",
  {"solve": "P", "F": "1000000", "r": "5.2", "m": "52", "yrs": "30"})

Q("Assignment 1", "Problem 4", "Cash Flow Diagram",
  """Prepare a cash flow diagram based on the following cash flows:
   Time Period (year)    Cash Flow
   t=0                   Receive _____$
   t=1                   Payment of _____$
   t=2                   Payment of _____$
   t=3                   Receive _____$
   t=4                   Receive _____$
   t=5                   Receive _____$
   t=6                   Payment of _____$""",
  {"cf": "1000, -4000, -4000, 5000, 5000, 5000, -6000", "upto": "7"})

# ---------------------------------------------------------------- Assignment 2
Q("Assignment 2", "Problem 1", "Depreciation (SLD and DBD)",
  """(a) Using declining-balance depreciation with d=__%, what is the book value after _ years for an asset costing _____$? What is the depreciation charge in the _th year?
(b) What is the depreciation rate using declining-balance for an asset costing _____$ and having a salvage value of _____$ after _ years?""",
  {"P": "500000", "S": "0", "N": "10", "dsrc": "Given d", "d": "10", "n": "5"},
  tip="Part (a) is set up (d given, year n = 5; 'Charge in the NEXT year' is the 6th-year charge). For part (b) change "
      "'Declining-balance rate d' to 'From salvage value' and type the cost, salvage, life and year n = life.")

Q("Assignment 2", "Problem 2", "DBD Rate that Matches SLD",
  """A machine costs _____$ and has a planned life of _ years, with a salvage value of _____$ (at the end of _ years) using straight-line depreciation. What depreciation rate will result in the same book values, for both the declining-balance and straight-line methods at the end of year _?""",
  {"P": "800000", "S": "200000", "N": "10", "n": "8"})

Q("Assignment 2", "Problem 3", "Compound Interest with Nominal Rate",
  """How much should you invest today (one time deposit) at __% interest (compounded ________) to accumulate _____$ in _ years? Instead of using the discounting formula, first find the factor value (P/F, i, N) = ?
   F = ?        i = ?        N = ?        (P/F, i, N) = ?""",
  {"solve": "P", "F": "250000", "r": "4", "m": "4", "yrs": "15"},
  tip="Press the 'Solve with' button in the top corner to flip between the exact formula and the printed-table factor (P/F, 1%, 60).")

Q("Assignment 2", "Problem 4", "Uneven Cash Flows (any time)",
  """Based on the following cash flow diagram, compute the equivalent net value of these cash flows at n=_ (yr), using i=__% (EAR annually).
   t=0: receive _____$     t=2: receive _____$     t=3: pay _____$     t=5: pay _____$     t=6: receive _____$
   F_ = ?""",
  {"i": "5", "T": "4", "cf": "8000, 0, 20000, -20000, 0, -20000, 25000"})

Q("Assignment 2", "Problem 5", "Net Sum at Time T (varying rates)",
  """Based on the following cash flows with various period interest rates (as shown in the Cash Flow Diagram), find the Net Sum as of period _, i.e., F_ = ?
   Cash flows:  t=0 pay _____$ ;  t=1 receive _____$ ;  t=3 receive _____$ ;  t=7 pay _____$ ;  t=8 receive _____$
   Period rates:  periods 1-3: __% ;  periods 4-5: __% ;  periods 6-8: __%""",
  {"cf": "-20000, 40000, 0, 40000, 0, 0, 0, -20000, 50000", "rates": "3, 3, 3, 5, 5, 7, 7, 7", "T": "5"},
  tip="Rates read off the diagram: 3%, 3%, 3%, then 5%, 5%, then 7%, 7%, 7% (one rate for each of periods 1 to 8).")

# ---------------------------------------------------------------- Assignment 3
Q("Assignment 3", "Problem 1", "Equal Deposits to Reach a Goal",
  """You want _____$ _ years from now for a European trip, and your grandparents give you _____$ today. You plan to make _ additional equal deposits from your part-time job, one deposit at the end of each year. If the bank pays __% interest compounded annually (EAR), how much must your annual deposits be?""",
  {"F": "25000", "P0": "10000", "i": "3", "N": "5"})

Q("Assignment 3", "Problem 2", "Annuity Segments, Value at Time T",
  """A Windsor engineer has developed an app for old photos editing and touchup. PHONTO-Tek, a Canadian software company, has offered her _____$ per year for the first _ years (years 1-_) and then _____$ per year for the following _ years (years _-_) for the rights to use her app. With annual interest rate (EAR) of __%, how much is this offer worth today (t=0)?""",
  {"i": "5", "amts": "50000, 70000", "ss": "1, 6", "es": "5, 10", "Ts": "0"})

Q("Assignment 3", "Problem 3", "Annuity Segments, Value at Time T",
  """Based on Problem 2, if the company PHONTO-Tek has offered an alternative payment plan that will defer its first payment till the end of year _, and pays this engineer _____$ per year for _ years (years _ to _), what is the Present Worth of this alternative payment plan (t=0)? The annual interest rate (EAR) is still __%.""",
  {"i": "5", "amts": "60000", "ss": "6", "es": "20", "Ts": "0"})

Q("Assignment 3", "Problem 4", "Maximum Price to Pay",
  """A new gantry profile laser cutting machine is expected to save ABC-Tool Inc. _____$ per year through reduced labor costs and increased quality. The device will have a life of _ years, and have a salvage value of _____$ at the end of the _th year (salvage value means the used machine can be sold in the open market). If the company can generally expect to get __% return on its capital, how much could it afford to pay for this laser cutting machine (i.e., what is the maximum price for this gantry laser cutting machine that ABC can afford)?""",
  {"A": "50000", "S": "80000", "N": "10", "i": "6"})

Q("Assignment 3", "Problem 5", "Deposits to Fund Later Withdrawals",
  """John and Ella are planning for an education fund for their new born daughter's university expense. They plan to deposit a fixed amount of money ($A dollars) starting at the end of year _ for _ years (t=_ to _ years). After year _, no additional fund will be deposited. The fund will earn __% interest rate annually based on the balance in this education account till it has been emptied. John and Ella figure that they need to make _ equal annual withdrawals in the amount of _____$ at the end of years _, _, _, and _. Find the value for A so the balance will be zero at the end of year _.""",
  {"i": "5", "ds": "1", "de": "10", "W": "30000", "ws": "18", "we": "21"})

# ---------------------------------------------------------------- Assignment 4
Q("Assignment 4", "Problem 1", "Loan Payment & Payoff Time",
  """Beth just bought sets of solar panels to power the ventilation and heating in her _ ft2 greenhouses in Leamington. The panels cost _____$ today and will reduce her electricity bills by average of _____$ per month (average of four seasons). How long will it take her to recoup her investment in the panels if she can earn __% interest, compounded ________, on her money? (Hint: You can use either the Capital Recovery Factor formula or the Series Present Worth Factor formula, or you can use linear interpolation!)""",
  {"P": "24000", "r": "6", "m": "12", "Np": "0", "A": "600"},
  tip="EQUATIONS = exact formula N. TABLES = linear interpolation between the two (P/A, 0.5%, N) table rows, as the hint suggests. "
      "'Planned number of payments' stays 0 here.")

Q("Assignment 4", "Problem 2", "Loan Payment & Payoff Time",
  """Your company has a _____$ loan for a new plasma soldering machine. The interest rate for this loan is __% per year and your company's initial plan is to pay for the loan in exactly _ years. Annual payment is made at the end of each year.
   (a) What is the Annual Payment your company has to pay at the end of each year?
   (b) Based on the initial annual payment calculated above, your company has now decided that it can afford to pay _____$ per year, which is more than the initial annual payment. By paying _____$ annually, how many payments (years) will the loan of _____$ be paid off? Assuming the same interest rate is used, i.e. __% annually.
   (c) By paying _____$ annually, how much is the last payment?""",
  {"P": "800000", "r": "5", "m": "1", "Np": "15", "A": "100000"})

Q("Assignment 4", "Problem 3", "Bond Price",
  """A bond with face value of _____$ pays quarterly interest of __% each quarter. _ interest payments (quarterly) remain before the bond matures. How much would you be willing to pay for this bond today if the next interest payment is due now and you want to earn annually __% but compounded ________ on your money?   Note: Coupon Rate is __% per quarter. Interest rate is __% per quarter. (interest payments starts with period 0 and finish at t=_)""",
  {"face": "5000", "cr": "1.5*4", "m": "4", "yrs": "26/4", "mr": "8", "first": "Now"},
  tip="The coupon is typed per YEAR: 1.5% per quarter -> 1.5*4.  Years to maturity: 26 payments / 4 per year -> 26/4.  "
      "First coupon = 'Now (t=0)'.")

Q("Assignment 4", "Problem 4", "Arithmetic Gradient",
  """For the following cash flows, calculate the Present Worth at an interest rate of __%.
   End of year 1: _____$    year 2: _____$    year 3: _____$    year 4: _____$    year 5: _____$    (a straight-line decrease)""",
  {"i": "10", "N": "5", "A1": "12000", "G": "2000", "dir": "Decreasing", "k": "0", "T": "0", "C0": "0", "div": "1"})

Q("Assignment 4", "Problem 5", "Arithmetic Gradient",
  """A UK lottery prize pays _____$ at the end of the first year, _____$ at the end of the second year, _____$ at the end of the third year, and so on for _ years. If this is the only prize in a lottery, _ tickets are sold, and you could invest your money elsewhere at __% interest rate (compounded annually), how much is each ticket worth, on average?""",
  {"i": "10", "N": "25", "A1": "5000", "G": "1000", "dir": "Increasing", "k": "0", "T": "0", "C0": "0", "div": "100000"},
  tip="'Present worth per share / ticket' is the answer: the prize's present worth divided by the number of tickets.")

# ---------------------------------------------------------------- Assignment 5
Q("Assignment 5", "Problem 1", "Capital Recovery Cost",
  """Amanda is looking at an investment in upgrading an inspection line at her plant. The initial cost would be _____$ with a salvage value of _____$ after _ years. Use the capital recovery formula to determine how much money must be saved every year to justify the investment at __% annual interest rate.""",
  {"P": "500000", "S": "100000", "N": "5", "i": "8", "B": "0"})

Q("Assignment 5", "Problem 2", "Capitalized Cost & Toll Recovery",
  """City of Toronto has built a bypass around the GTA downtown CBD area. The cost of construction of this bypass system costs the city totally _____$ million (present value). In addition, once the bypass starts its operation this year, the annual maintenance cost is estimated to be _____$ per year for many years ahead. If properly maintained, this bypass can be operational indefinitely.
First, calculate the Present Cost (both construction and maintenance cost for many years ahead) using the annual interest rate of __%.
Second, the city wants to recover the cost (construction and maintenance altogether) by collecting tolls. If the tolls collected annually remain to be a constant and the city wishes to recover the cost in exactly _ years (from now till the end of the _ year), how much tolls should be collected each year? Interest rate is still __% annually.""",
  {"C": "100000000", "M": "800000", "i": "5", "N": "15"},
  tip="Type the construction cost in full dollars: $100 million = 100000000.")

Q("Assignment 5", "Problem 3", "Geometric Gradient",
  """Engineers at La Ronde, the largest amusement park in Montreal, are considering an innovation on the existing Monster roller coaster to make it more exciting. The modification has been completed. However, the manager wishes to know the Present Value of the maintenance cost. The maintenance cost for the 1st quarter is _____$ and is expected to grow __% per quarter for the next _ years (totally _ quarters). The manager is using the annual interest rate of __%, compounded ________.
First, find the growth adjusted interest rate i0 = ?
Second, find the Present Value of the next _ years maintenance cost.""",
  {"i": "4/4", "g": "3", "N": "20", "A": "5000", "k": "0", "T": "0"},
  tip="The interest rate per quarter is typed as 4/4 (annual rate / 4). i0 is the first line of the answer.")

Q("Assignment 5", "Problem 4", "Geometric Gradient",
  """Alex is expecting steady growth of __% per year in profits from his new company. All profits are going to be invested and earn at __% interest annually. If profits for this year (at the end of the year) total _____$, how much will be saved at the end of _ years?""",
  {"i": "15", "g": "10", "N": "10", "A": "10000", "k": "0", "T": "10"},
  tip="'Equivalent value at time T' with T = the number of years is the amount saved.")

Q("Assignment 5", "Problem 5", "Account: Deposits, Extras and Fees",
  """Today, Jan. 1, 2026, Kobe starts an investment account and this account guarantees an interest rate of __%, compounded ________. To start, he first transfers his _____$ saving into this account so the account balance is _____$ on Jan. 1, 2026 (t= month 0). In addition, he will continue to add money to this account through two ways for totally _ years.
First, at the end of each month, he will deposit _____$ from his earnings to this account. First _____$ will be deposited on Jan. 31, 2026 (t=1) and last deposit of _____$ will be made on Dec. 31, 2030 (t=_), totally _ monthly deposits (_____$ each).
Second, his grandparents will transfer _____$ to this account once every _ months. First transfer will be made on June 30, 2026 (t=_) and last transfer will be made on Dec. 31, 2030 (t=_), totally _ transfer payments (_____$ each).
In addition, the financial institute which manages this account will charge monthly management fee and this fee will be deducted from the account at the end of each month. The fee for the first month (deducted on Jan. 31, 2026) will be _____$ and this fee is going to increase by _____$ per month thereafter. Therefore, the management fee for the last month of the _-year period (Dec. 31 2030) will be _____$. Find how much will be accumulated at the end of Dec. 31, 2030?""",
  {"r": "6", "m": "12", "N": "60", "P0": "3000", "A": "200", "B": "3000", "k": "6", "F1": "10", "G": "1"})

# ---------------------------------------------------------------- Quiz 1
Q("Quiz 1", "Question 1", "Compound Interest with Nominal Rate",
  """Amanda wants to make a withdrawal of _____$ exactly _ years from today. The bank is offering a GIC (Guaranteed Investment Certificate) with annual interest rate of __%, compounded ________. How much should Amanda deposit today in order to reach her goal?""",
  {"solve": "P", "F": "30000", "r": "5", "m": "12", "yrs": "5"},
  check="Original numbers: $23,376.16  (other choices were 23,505.78 / 24,595.33 / 29,335.22).")

Q("Quiz 1", "Question 2", "Compound Interest with Nominal Rate",
  """Today, Alex deposits _____$ in a GIC which pays annual interest rate of __%, compounded ________. How much does Alex expect to accumulate by the end of _ years?""",
  {"solve": "F", "P": "30000", "r": "6", "m": "365", "yrs": "2"},
  check="Original numbers: $33,824.57  (other choices were 31,987.44 / 32,996.58 / 34,785.61).")

Q("Quiz 1", "Question 3", "Compare Nominal Rates (EAR)",
  """Anne is trying to secure a car loan for the new car she intends to buy after her graduation. The amount she wants to borrow is _____$ CDN. Based on her research about the market, the following 2 financial institutions are to be considered and compared:
  - Sun Bank's car loan program, with nominal annual rate of __%, but compounded ________.
  - Moon Bank's car loan program, with nominal annual rate of __%, but compounded ________.
First, calculate the Effective Annual Rate (EAR) for the Sun Bank.
Second, calculate the Effective Annual Rate (EAR) for the Moon Bank.
Finally, based on the EAR between the Sun Bank and the Moon Bank, which Bank should Anne select in order to minimize the cost of borrowing?""",
  {"rs": "7.25, 7.00", "ms": "2, 365", "goal": "Lowest", "P": "30000", "yrs": "1"},
  tip="Option 1 = Sun Bank, Option 2 = Moon Bank. The 'amount owed after years' line only matters if you also want a balance.",
  check="Original numbers: Sun EAR = 7.3814%, Moon EAR = 7.2501%  ->  select the Moon Bank.")

# ---------------------------------------------------------------- Quiz 2
Q("Quiz 2", "Question 1", "DBD Rate that Matches SLD",
  """Windsor Tool Inc. has purchased a new 300-ton press machine which costs _____$. The accounting department estimates that the life of this machine to be exactly _ years and the salvage value at the end of the _-th year to be _____$.
First, using the Straight-Line Depreciation (SLD) method, what is the book value of this press machine at the end of year _?
Second, if we use the Declining Balance Depreciation (DBD) method, we find that the book value at the end of year _ is the same as using the SLD method. Based on this information, what is the depreciation rate d = ?""",
  {"P": "600000", "S": "100000", "N": "10", "n": "6"},
  check="Original numbers: Book value (SLD, end of year 6) = $300,000;  d = 10.9101%.")

Q("Quiz 2", "Question 2", "Net Sum at Time T (varying rates)",
  """Based on the following Cash Flow Diagram with various period interest rates (as shown in the diagram), find the Net Sum as of Period _, i.e., F_ = ?
   Cash flows:  t=0 pay _____$ ;  t=3 receive _____$ ;  t=7 pay _____$ ;  t=8 receive _____$
   Period rates:  periods 1-3: __% ;  periods 4-6: __% ;  periods 7-8: __%""",
  {"cf": "-3000, 0, 0, 5000, 0, 0, 0, -3000, 6000", "rates": "3, 3, 3, 7, 7, 7, 8, 8", "T": "6"},
  tip="Rates read off the diagram: 3%, 3%, 3%, then 7%, 7%, 7%, then 8%, 8% (periods 1 to 8).",
  check="Original numbers: F6 = $4,475.56  (other choices were 4,203.61 / 4,302.14 / 4,574.47 / 4,633.32).")

# ---------------------------------------------------------------- Quiz 3
Q("Quiz 3", "Question 1", "Annuity Segments, Value at Time T",
  """According to the cash flows given below (also shown in the Cash Flow Diagram), find the Net Sum according to the time periods specified. Interest rate is given as __% per year (period).
   Years 0 to _       No Cash Flow
   Years _ to _       _____$ per year
   Years _ to _       No Cash Flow
   Years _ to _       _____$ per year
   Years _ to _       No Cash Flow
First, find Net Sum at the end of Year _ (i.e., P_ = ?)
Second, find the Net Sum at the end of Year _, (i.e., F_ = ?)""",
  {"i": "5", "amts": "10000, 20000", "ss": "6, 31", "es": "20, 45", "Ts": "2, 55"},
  check="Original numbers: P2 = $142,623.28;  F55 = $1,893,298.30.")

Q("Quiz 3", "Question 2", "Capital Recovery Cost",
  """A new injection molding machine cost ABC-Plastic _____$ today. It can be used for _ years. At the end of the _th year, it can be sold to the open market for _____$. If the annual interest rate is __%, what Net Annual Savings are needed to justify this purchase?""",
  {"P": "600000", "S": "150000", "N": "8", "i": "6", "B": "0"},
  check="Original numbers: Annual savings should be more than $81,468.")

# ---------------------------------------------------------------- Quiz 4
Q("Quiz 4", "Question 1", "Loan Payment & Payoff Time",
  """Your company has received a _____$ loan for the purchase of a new 6-axis CNC machining center. The interest rate for this loan is __% per year. Your company initial plan is to pay off this loan in exactly _ years with _ equal annual payments made at the end of the _ years.
Check the Screen for the complete question and answer the following.
[The on-screen part was not in the files. The answer choices show it asks for:]
   - Annual Payment (_ equal payments) = ?
   - Payback in how many years  (paying _____$ per year)?
   - Last Payment (when paying _____$ in the earlier years) = ?""",
  {"P": "700000", "r": "9", "m": "1", "Np": "15", "A": "100000"},
  tip="The $100,000 per year is taken from the wording of the answer choices ('Last Payment when pay $100,000 earlier years'). "
      "If the on-screen question uses another payment, type it into 'Payment you actually make'.",
  check="Original numbers: Annual payment = $86,842;  payback in 12 years;  last payment = $54,791.44.")

Q("Quiz 4", "Question 2", "Bond Price",
  """A bond with a face value of _____$ pays a coupon rate of __% per quarter. This bond will mature in _ years (_ quarters) and the first interest payment is exactly one quarter from now. The annual interest rate is __%, compounded ________. With those information, find the Present Worth (PW) of this bond.""",
  {"face": "10000", "cr": "1.0*4", "m": "4", "yrs": "25", "mr": "8", "first": "End"},
  tip="The coupon is typed per YEAR: 1% per quarter -> 1.0*4. First coupon = 'End of period 1'.",
  check="Original numbers: PW = $5,690.10  (other choices were 7,845.00 / 12,581.30 / 13,151.45 / 16,302.90).")

# ---------------------------------------------------------------- Quiz 5
Q("Quiz 5", "Question 1", "Capital Recovery Cost",
  """A new injection molding machine cost ABC-Plastic _____$ today. It can be used for _ years. At the end of the _th year, it can be sold to the open market for _____$. If the annual interest rate is __%, what Net Annual Savings are needed to justify this purchase?""",
  {"P": "800000", "S": "150000", "N": "8", "i": "9", "B": "0"},
  check="Original numbers: Annual savings should be more than $130,935.5.")

Q("Quiz 5", "Question 2", "Quarterly Gradient",
  """Amanda has opened an investment account for her family that guarantees annual interest rate __%, compounded ________.
She plans to deposit _____$ a quarter from today (end of the 1st quarter). Subsequent quarterly deposits will increase by _____$ per quarter for a total of _ years (_ quarterly deposits as an arithmetic gradient series with G=_____$). The last deposit at the end of year _ (end of quarter _) will be _____$ + (_____ x _).
Amanda will stop adding fund to this account after _ years and she will start the 1st annual withdrawal at the end of Year _ in the amount of X dollars.
She will empty the account by making _ annual withdrawals at the end of year _, _, etc. Last annual withdrawal will be made at the end of year _. These _ annual withdrawals will increase by __% annually, i.e., X for year _, X(1+__%) for year _, X(1+__%)^2 for year _, ... and X(1+__%)^_ for year _.
First, find out how much has been accumulated at the end of Year _ (right after the _th quarterly deposit).
Second, find the value for X, the amount of the first annual withdrawal at the end of year _.""",
  {"r": "6", "m": "4", "A1": "12000", "G": "600", "Nq": "60", "g": "2", "Nw": "15"},
  tip="Quarterly deposits are an arithmetic gradient (A' = first deposit, G = increase); the withdrawals are a geometric gradient "
      "that starts at X one year after the last deposit.",
  check="Original numbers (answer key): accumulated 2,603,173.8;  X = 239,739.2.  Press 'Solve with: TABLES' to reproduce the key "
        "(2,603,173.80 and 239,738.49); exact equations give 2,603,161.89 and 239,737.40.")


# =====================================================================
#  UI
# =====================================================================
WHITE = (255, 255, 255)

LIGHT = dict(
    BG=(244, 246, 250), PANEL=(255, 255, 255), PANEL2=(238, 241, 247), INK=(28, 32, 40), GREY=(110, 118, 130),
    LINE=(200, 206, 216), ACCENT=(31, 56, 100), HEADER=(31, 56, 100), HDR_SUB=(200, 212, 235),
    INPUT_BG=(255, 250, 205), INPUT_FOCUS=(255, 241, 150), INPUT_OFF=(235, 238, 244), OFFTXT=(160, 166, 176),
    KEY_BG=(226, 240, 217), ERR=(190, 40, 40), NOTE=(70, 90, 120), HOVER=(222, 232, 250),
    TBL_HEAD=(217, 225, 242), TBL_ALT=(246, 248, 252), SEL=(120, 170, 240), PLACE=(150, 156, 168),
    SB_TRACK=(226, 230, 238), SB_THUMB=(160, 170, 190), SB_HOT=(110, 124, 150),
    BTN=(222, 232, 250), BTN_TXT=(31, 56, 100), BTN_ON=(46, 140, 80), POS=(0, 140, 80), NEG=(200, 50, 50),
    QBOX=(255, 252, 235),
)
DARK = dict(
    BG=(18, 20, 24),           # Deep, clean neutral dark background
    PANEL=(28, 32, 38),        # Elevated panel cards / containers
    PANEL2=(38, 44, 52),       # Secondary card layer
    INK=(240, 242, 245),       # Primary text (high contrast white-grey)
    GREY=(150, 158, 172),      # Secondary/muted text
    LINE=(65, 74, 88),         # Crisp borders and dividers
    ACCENT=(96, 150, 235),     # Vibrant interactive blue accent
    HEADER=(24, 30, 40),       # Header background
    HDR_SUB=(170, 184, 208),   # Header subtext
    
    # Inputs (Fixed: high contrast text vs background)
    INPUT_BG=(42, 48, 58),     # Input field background
    INPUT_FOCUS=(55, 85, 130), # Input field when active/focused
    INPUT_OFF=(26, 30, 36),    # Disabled input background
    OFFTXT=(100, 110, 125),    # Disabled text
    
    KEY_BG=(35, 90, 60),       # Key/badge background (muted green)
    ERR=(245, 95, 95),         # Error state (vibrant red)
    NOTE=(120, 175, 230),      # Informational note highlight
    HOVER=(50, 62, 78),        # Hover state highlight
    
    TBL_HEAD=(45, 56, 72),     # Table header background
    TBL_ALT=(24, 28, 34),      # Alternating table row background
    SEL=(40, 100, 200),        # Selection highlight blue
    PLACE=(120, 130, 145),     # Placeholder text color
    
    # Scrollbar
    SB_TRACK=(30, 35, 42),     # Scrollbar track
    SB_THUMB=(85, 98, 118),    # Scrollbar thumb
    SB_HOT=(120, 138, 165),    # Scrollbar thumb hover
    
    # Buttons & Status Indicators
    BTN=(55, 68, 88),          # Standard button background
    BTN_TXT=(240, 245, 250),   # Button text color
    BTN_ON=(40, 145, 85),      # Active/On button state
    POS=(60, 210, 120),        # Positive values (clean green)
    NEG=(255, 90, 90),         # Negative values (clean red)
    QBOX=(55, 50, 35),         # Question box background (muted warm dark)
)
DARK_MODE = [False]


def set_theme(dark):
    DARK_MODE[0] = bool(dark)
    globals().update(DARK if dark else LIGHT)


set_theme(False)


def make_font(names, size, bold=False, italic=False):
    return pygame.font.SysFont(names, size, bold=bold, italic=italic)


class Fonts:
    def __init__(self):
        ui = "segoeui,helveticaneue,arial,dejavusans,liberationsans"
        mono = "consolas,menlo,couriernew,dejavusansmono,liberationmono"
        self.title = make_font(ui, 24, True)
        self.h1 = make_font(ui, 18, True)
        self.ui = make_font(ui, 16)
        self.uib = make_font(ui, 16, True)
        self.small = make_font(ui, 14)
        self.smalli = make_font(ui, 14, italic=True)
        self.mono = make_font(mono, 15)
        self.monos = make_font(mono, 14)
        self.monob = make_font(mono, 15, True)


_WRAP = {}


def wrap(text, font, width):
    key = (text, font, width)
    hit = _WRAP.get(key)
    if hit is not None:
        return hit
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
    if len(_WRAP) > 600:
        _WRAP.clear()
    _WRAP[key] = lines
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
        pygame.draw.rect(surf, SB_TRACK, track, border_radius=5)
        h = max(34, int(track.h * track.h / (track.h + maxs)))
        y = track.y + int((track.h - h) * min(1.0, scroll / maxs))
        self.thumb = pygame.Rect(track.x, y, track.w, h)
        hot = self.drag is not None or self.thumb.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, SB_HOT if hot else SB_THUMB, self.thumb, border_radius=5)

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


# ---- buttons in the top corner ------------------------------------------------
HDR_BTN = {}   # name -> Rect, refilled on every frame


def _btn(surf, f, name, text, right, y, on=False):
    t = f.small.render(text, True, WHITE if on else BTN_TXT)
    r = pygame.Rect(0, y, t.get_width() + 22, 24)
    r.right = right
    pygame.draw.rect(surf, BTN_ON if on else BTN, r, border_radius=6)
    pygame.draw.rect(surf, HDR_SUB, r, 1, border_radius=6)
    surf.blit(t, (r.x + 11, r.y + 4))
    HDR_BTN[name] = r
    return r


def draw_header_buttons(surf, f, W, y, show_cont=False, cont_on=False):
    """Dark-mode button (far right), equations/tables button, and - only on the Nominal -> Effective
    screen - the continuous-compounding button on a second row. Returns the left edge of them all."""
    r1 = _btn(surf, f, "dark", "Dark mode: " + ("ON" if DARK_MODE[0] else "OFF"), W - 14, y, on=DARK_MODE[0])
    r2 = _btn(surf, f, "table", "Solve with: " + ("TABLES" if TABLE_MODE[0] else "EQUATIONS"), r1.left - 8, y, on=TABLE_MODE[0])
    left = r2.left
    if show_cont:
        r3 = _btn(surf, f, "cont", "Continuous compounding: " + ("ON" if cont_on else "OFF"), W - 14, y + 28, on=cont_on)
        left = min(left, r3.left)
    return left


def toggle_dark():
    set_theme(not DARK_MODE[0])


def toggle_table(screens):
    TABLE_MODE[0] = not TABLE_MODE[0]
    for s in screens.values():
        s.recalc()


class CalcScreen:
    def __init__(self, calc, fonts, q=None):
        self.c, self.f, self.q = calc, fonts, q
        self.scroll = 0
        self.content_h = 0
        self.view_h = 400
        self.maxs = 0
        self.bar = ScrollBar()
        self.lscroll, self.lmax, self.follow = 0, 0, False
        self.lbar = ScrollBar()
        self.area_l = pygame.Rect(0, 0, 0, 0)
        self.rx = 10 ** 6
        self.cont = False
        self.diag = None
        self.reset()

    # ---- state
    def reset(self):
        pre = self.q.preset if self.q else {}
        self.text, self.cur, self.choice = {}, {}, {}
        for fl in self.c.fields:
            if fl.kind == "choice":
                idx = 0
                if fl.key in pre:
                    for j, o in enumerate(fl.opts):
                        if o == pre[fl.key] or o.startswith(str(pre[fl.key])):
                            idx = j
                            break
                self.choice[fl.key] = idx
            else:
                d = str(pre.get(fl.key, fl.default))
                self.text[fl.key] = d
                self.cur[fl.key] = len(d)
        self.focus = 0
        self.fresh = True
        self.act = None
        self.outs, self.table, self.err, self.diag = [], None, None, None
        self.scroll = 0
        self.lscroll = 0
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

    def toggle_cont(self):
        self.cont = not self.cont
        self.recalc()

    def recalc(self):
        v = dict(self.choices())
        v["_cont"] = self.cont
        try:
            self.act = self.c.active(v) if self.c.active else None
        except Exception:
            self.act = None
        self.err, self.diag = None, None
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
                elif fl.kind == "pct":
                    v[fl.key] = parse_num(self.text[fl.key]) / 100.0
                else:
                    v[fl.key] = parse_num(self.text[fl.key])
            except Exception:
                self.err = "Can't read the value for:  " + fl.label
                self.outs, self.table = [], None
                return
        try:
            TBL[0], MISS[0] = TABLE_MODE[0], False
            res = self.c.fn(v)
            missed = MISS[0]
            TBL[0] = False
            self.outs, self.table = res[0], res[1]
            self.diag = res[2] if len(res) > 2 else None
            if not self.c.notable:
                self._add_other_way(v)
                if TABLE_MODE[0] and missed:
                    self.outs = [N("TABLE mode: this rate or N is not in the printed tables for some factors "
                                   "(tables use rates in steps of 0.25% and N = 1 to 100), so the exact formula was used for those.")] + self.outs
        except InputError as e:
            self.err, self.outs, self.table = str(e), [], None
        except ZeroDivisionError:
            self.err, self.outs, self.table = "Division by zero - check the inputs (a rate or N of 0?)", [], None
        except (ValueError, OverflowError):
            self.err, self.outs, self.table = "These inputs have no valid answer - check them.", [], None
        except Exception as e:  # never crash the exam
            self.err, self.outs, self.table = "Error: %s" % e, [], None
        finally:
            TBL[0] = False

    def _add_other_way(self, v):
        """Run the calculator the OTHER way (tables if equations are selected, and the reverse) and
        keep that answer under each result that differs."""
        TBL[0], MISS[0] = not TABLE_MODE[0], False
        try:
            other = self.c.fn(v)[0]
        except Exception:
            other = []
        finally:
            TBL[0] = False
        label = "with equations" if TABLE_MODE[0] else "with table factors"
        if len(other) == len(self.outs):
            for o, ot in zip(self.outs, other):
                if o.style in ("key", "norm") and ot.label == o.label and ot.value != o.value:
                    o.tv, o.tlabel = ot.value, label

    def move_focus(self, d):
        n = len(self.c.fields)
        if not n:
            return
        k = self.focus
        for _ in range(n):
            k = (k + d) % n
            if self.is_active(self.c.fields[k]):
                break
        self.focus, self.fresh, self.follow = k, True, True

    # ---- events
    def on_event(self, e, rect_cache):
        s = self.bar.event(e, self.scroll, self.maxs)
        if s is not None:
            self.scroll = s
            return None
        s = self.lbar.event(e, self.lscroll, self.lmax)
        if s is not None:
            self.lscroll = s
            return None
        fl = self.c.fields[self.focus] if self.c.fields else None
        if e.type == pygame.MOUSEWHEEL:
            if pygame.mouse.get_pos()[0] < self.rx - 8:
                self.lscroll = max(0, min(self.lmax, self.lscroll - e.y * 48))
            else:
                self.scroll = max(0, min(max(0, self.content_h - self.view_h), self.scroll - e.y * 48))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if not self.area_l.collidepoint(e.pos):
                return None
            for k, (r, _unused) in rect_cache.items():
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
        HDR_BTN.clear()
        pygame.draw.rect(surf, HEADER, (0, 0, W, 58))
        bl = draw_header_buttons(surf, f, W, 6, show_cont=self.c.contbtn, cont_on=self.cont)
        surf.blit(f.title.render(clip_text(f.title, self.c.title, bl - 40), True, WHITE), (20, 5))
        sub = ("%s - %s" % (self.q.group, self.q.label)) if self.q else self.c.desc
        surf.blit(f.small.render(clip_text(f.small, sub, bl - 40), True, HDR_SUB), (22, 36))

        lw = min(580, int(W * 0.46))
        left = 20
        rx = left + lw + 24
        self.rx = rx
        rw = W - rx - 20
        area_l = pygame.Rect(0, 62, rx - 10, H_ - 62 - 32)
        self.area_l = area_l
        cache = {}

        # ---- left panel (scrolls on its own): inputs, question, formulas, notes
        prev = surf.get_clip()
        surf.set_clip(area_l)
        y = 70 - self.lscroll
        ftop = fbot = None
        for k, fl in enumerate(self.c.fields):
            act = self.is_active(fl)
            col = INK if act else OFFTXT
            if fl.wide:   # label on its own line, full-width input box under it
                surf.blit(f.ui.render(clip_text(f.ui, fl.label, lw), True, col), (left, y + 2))
                box = pygame.Rect(left, y + 26, lw, 30)
                rowh = 64
            else:
                surf.blit(f.ui.render(clip_text(f.ui, fl.label, lw - 215), True, col), (left, y + 5))
                box = pygame.Rect(left + lw - 205, y, 205, 30)
                rowh = 38
            cache[k] = (box, True)
            if k == self.focus:
                ftop, fbot = y, y + rowh
            if fl.kind == "choice":
                pygame.draw.rect(surf, INPUT_FOCUS if (k == self.focus and act) else PANEL2, box, border_radius=5)
                pygame.draw.rect(surf, ACCENT if k == self.focus else LINE, box, 2 if k == self.focus else 1, border_radius=5)
                surf.blit(f.uib.render("<", True, ACCENT), (box.x + 8, box.y + 4))
                surf.blit(f.uib.render(">", True, ACCENT), (box.right - 18, box.y + 4))
                txt = clip_text(f.ui, fl.opts[self.choice[fl.key]], box.w - 44)
                ts = f.ui.render(txt, True, INK)
                surf.blit(ts, (box.centerx - ts.get_width() // 2, box.y + 5))
            else:
                focused = (k == self.focus and act)
                bgc = INPUT_OFF if not act else (INPUT_FOCUS if focused else INPUT_BG)
                pygame.draw.rect(surf, bgc, box, border_radius=5)
                pygame.draw.rect(surf, ACCENT if focused else LINE, box, 2 if focused else 1, border_radius=5)
                txt = self.text[fl.key]
                avail = box.w - 14
                shown, off = txt, 0
                if f.mono.size(txt)[0] > avail:  # keep the cursor visible
                    c = self.cur[fl.key]
                    while f.mono.size(txt[off:c])[0] > avail - 4 and off < len(txt):
                        off += 1
                    shown = txt[off:]
                ts = f.mono.render(shown, True, INK if act else GREY)
                if focused and self.fresh and txt:
                    pygame.draw.rect(surf, SEL, (box.x + 6, box.y + 4, min(ts.get_width(), avail), 22))
                surf.blit(ts, (box.x + 7, box.y + 5), area=pygame.Rect(0, 0, avail, 24))
                if focused and not self.fresh and (pygame.time.get_ticks() // 500) % 2 == 0:
                    cx = box.x + 7 + f.mono.size(txt[off:self.cur[fl.key]])[0]
                    pygame.draw.line(surf, INK, (cx, box.y + 5), (cx, box.y + 25), 2)
            y += rowh
        if self.c.fields:
            fh = self.c.fields[self.focus].hint
            if fh:
                for ln in wrap(fh, f.smalli, lw):
                    surf.blit(f.smalli.render(ln, True, GREY), (left, y))
                    y += 18
        y += 8

        def box_(y, title, body, font, col, bg=PANEL, tcol=None):
            lines = wrap(body, font, lw - 24)
            lh = font.get_linesize() + 1
            bh = len(lines) * lh + 34
            pygame.draw.rect(surf, bg, (left, y, lw, bh), border_radius=6)
            pygame.draw.rect(surf, LINE, (left, y, lw, bh), 1, border_radius=6)
            surf.blit(f.small.render(title, True, tcol or ACCENT), (left + 10, y + 6))
            for n_, ln in enumerate(lines):
                surf.blit(font.render(ln, True, col), (left + 12, y + 28 + n_ * lh))
            return bh + 10

        if self.q:
            y += box_(y, "%s - %s   (question; every number is a blank)" % (self.q.group, self.q.label),
                      self.q.text, f.small, INK, bg=QBOX)
            if self.q.tip:
                y += box_(y, "How to use this screen", self.q.tip, f.small, NOTE)
            if self.q.check:
                y += box_(y, "Check with the original numbers (they are pre-filled above)", self.q.check, f.small, NOTE)
        if self.c.formula:
            y += box_(y, "Formulas", self.c.formula, f.monos, INK)
        if self.c.note:
            y += box_(y, "Notes / slide checks", self.c.note, f.small, NOTE)
        self.lmax = max(0, y + self.lscroll - 70 + 6 - area_l.h)
        surf.set_clip(prev)
        if self.follow and ftop is not None:
            if ftop < area_l.top + 4:
                self.lscroll = max(0, self.lscroll - (area_l.top + 4 - ftop))
            elif fbot > area_l.bottom - 4:
                self.lscroll = min(self.lmax, self.lscroll + (fbot - (area_l.bottom - 4)))
            self.follow = False
        self.lscroll = max(0, min(self.lscroll, self.lmax))
        self.lbar.draw(surf, pygame.Rect(left + lw + 8, 66, 8, area_l.h - 8), self.lscroll, self.lmax)

        # ---- results panel
        area = pygame.Rect(rx, 66, rw, H_ - 66 - 34)
        pygame.draw.rect(surf, PANEL, area, border_radius=8)
        pygame.draw.rect(surf, LINE, area, 1, border_radius=8)
        self.view_h = area.h
        prev = surf.get_clip()
        surf.set_clip(area.inflate(-2, -2))
        yy = area.y + 8 - self.scroll
        x0, x1 = area.x + 14, area.right - 24
        mode = "Solving with: PRINTED TABLE FACTORS (5 significant digits)" if TABLE_MODE[0] else "Solving with: EQUATIONS (exact formulas)"
        surf.blit(f.small.render(mode, True, ACCENT), (x0, yy))
        yy += 22
        if self.err:
            for ln in wrap(self.err, f.h1, rw - 40):
                surf.blit(f.h1.render(ln, True, ERR), (x0, yy))
                yy += 26
        for o in self.outs:
            if o.style == "head":
                yy += 4
                pygame.draw.rect(surf, HEADER, (area.x + 6, yy, rw - 24, 26), border_radius=4)
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
                if o.tv is not None:   # the same answer computed the other way
                    tl = f.smalli.render("%s:  %s" % (o.tlabel, o.tv), True, NOTE)
                    surf.blit(tl, (x1 - tl.get_width(), yy + rowh - 4))
                    rowh += 16
                yy += rowh
        if self.diag is not None:
            yy += 6
            self.diag.draw(surf, f, pygame.Rect(area.x + 6, yy, rw - 24, CFDiagram.HEIGHT))
            yy += CFDiagram.HEIGHT + 8
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
            pygame.draw.rect(surf, TBL_HEAD, (area.x + 6, yy, min(tw, rw - 24), 24))
            xx = tx
            for c_, h in enumerate(hdrs):
                s_ = f.monos.render(h, True, ACCENT)
                surf.blit(s_, (xx + widths[c_] - s_.get_width() - 8, yy + 3))
                xx += widths[c_]
            yy += 26
            for ri, r in enumerate(rows):
                if yy + 22 > area.y and yy < area.bottom:
                    if ri % 2:
                        pygame.draw.rect(surf, TBL_ALT, (area.x + 6, yy, min(tw, rw - 24), 22))
                    xx = tx
                    for c_, cell in enumerate(r):
                        s_ = f.monos.render(cell, True, INK)
                        surf.blit(s_, (xx + widths[c_] - s_.get_width() - 8, yy + 2))
                        xx += widths[c_]
                yy += 22
            if tw > rw - 40:
                surf.blit(f.smalli.render("(table is wider than the window - enlarge the window)", True, GREY), (x0, yy))
                yy += 20
        self.content_h = yy + self.scroll - area.y + 10
        surf.set_clip(prev)
        self.maxs = max(0, self.content_h - area.h)
        self.scroll = min(self.scroll, self.maxs)
        self.bar.draw(surf, pygame.Rect(area.right - 14, area.y + 6, 10, area.h - 12), self.scroll, self.maxs)
        foot = "Esc = menu   Tab = next field   Ctrl+R = reset   Ctrl+D = dark mode   Ctrl+T = equations / tables"
        if self.c.contbtn:
            foot += "   Ctrl+K = continuous"
        foot += "   |   rates in percent (8 = 8%, 6/12 = 0.5%)"
        surf.blit(f.small.render(clip_text(f.small, foot, W - 40), True, GREY), (20, H_ - 26))
        return cache


class Menu:
    def __init__(self, fonts):
        self.f = fonts
        self.q = ""
        self.sel = 0
        self.scroll = 0
        self.maxs = 0
        self.bar = ScrollBar()
        self._rects = []
        self.moved = False

    def filtered(self):
        q = self.q.lower().strip()
        if not q:
            return CALCS, []
        words = q.split()
        cs = [c for c in CALCS if all(w in (c.title + " " + c.desc + " " + c.kw + " " + c.cat).lower() for w in words)]
        grp = re.match(r"^(assignment|quiz)\s*(\d)\b\s*(.*)$", q)   # "quiz 4", "assignment 2 problem 3"
        if grp:
            name = "%s %s" % (grp.group(1), grp.group(2))
            rest = grp.group(3).split()
            qs = [x for x in QUESTIONS if x.group.lower() == name
                  and all(w in (x.label + " " + x.calc.title + " " + x.text).lower() for w in rest)]
            return [], qs
        qs = [x for x in QUESTIONS
              if all(w in (x.group + " " + x.label + " " + x.calc.title + " " + x.text).lower() for w in words)]
        return cs, qs

    def layout(self, W):
        calcs, qs = self.filtered()
        cols = max(1, min(4, (W - 56) // 290))
        cw = (W - 56 - (cols - 1) * 14) // cols
        ch = 78
        y = 150
        rects, heads = [], []

        def grid(items, y):
            for n_, c in enumerate(items):
                rects.append((c, pygame.Rect(20 + (n_ % cols) * (cw + 14), y + (n_ // cols) * (ch + 12), cw, ch), "card"))
            return y + ((len(items) + cols - 1) // cols) * (ch + 12)

        if self.q.strip():
            if calcs:
                heads.append(("h", "CALCULATORS", y))
                y = grid(calcs, y + 32) + 8
            if qs:
                heads.append(("h", "QUESTIONS", y))
                y = grid(qs, y + 32) + 8
        else:
            for cat in CATS:
                cs = [c for c in calcs if c.cat == cat]
                if not cs:
                    continue
                heads.append(("h", cat.upper(), y))
                y = grid(cs, y + 32) + 8
            bw, bh, gap, x_start = 214, 52, 10, 150

            def rows(groups, y):
                for g in groups:
                    heads.append(("lab", g, y))
                    xs, yr = x_start, y
                    for x in [x for x in QUESTIONS if x.group == g]:
                        if xs + bw > W - 34:
                            xs, yr = x_start, yr + bh + gap
                        rects.append((x, pygame.Rect(xs, yr, bw, bh), "btn"))
                        xs += bw + gap
                    y = yr + bh + 14
                return y

            heads.append(("h", "ASSIGNMENTS  (tutorial problems - one button per question)", y))
            y = rows(GROUPS_A, y + 36) + 6
            heads.append(("h", "QUIZZES  (one button per question)", y))
            y = rows(GROUPS_Q, y + 36)
        return rects, heads, y

    def draw(self, surf):
        f = self.f
        W, H_ = surf.get_size()
        surf.fill(BG)
        HDR_BTN.clear()
        rects, heads, total = self.layout(W)
        self.sel = max(0, min(self.sel, max(0, len(rects) - 1)))
        maxs = max(0, total - H_ + 20)
        self.maxs = maxs
        self.scroll = max(0, min(self.scroll, maxs))
        if rects and not self.moved:   # keep the keyboard selection visible
            r = rects[self.sel][1]
            if r.top - self.scroll < 150:
                self.scroll = max(0, r.top - 160)
            elif r.bottom - self.scroll > H_ - 10:
                self.scroll = min(maxs, r.bottom - H_ + 20)
        for kind, text, y in heads:
            yy = y - self.scroll
            if yy < 112:
                continue
            if kind == "h":
                surf.blit(f.h1.render(text, True, ACCENT), (22, yy + 4))
                pygame.draw.line(surf, LINE, (20, yy + 28), (W - 36, yy + 28))
            else:
                surf.blit(f.uib.render(text, True, INK), (22, yy + 15))
        mx, my = pygame.mouse.get_pos()
        for n_, (c, r, style) in enumerate(rects):
            rr = r.move(0, -self.scroll)
            if rr.bottom < 140 or rr.top > H_:
                continue
            if rr.collidepoint(mx, my) and my > 140 and pygame.mouse.get_focused() and self.moved:
                self.sel = n_
            on = n_ == self.sel
            pygame.draw.rect(surf, HOVER if on else PANEL, rr, border_radius=8)
            pygame.draw.rect(surf, ACCENT if on else LINE, rr, 2 if on else 1, border_radius=8)
            if isinstance(c, Question):
                if style == "btn":
                    surf.blit(f.uib.render(c.label, True, ACCENT), (rr.x + 10, rr.y + 7))
                    surf.blit(f.small.render(clip_text(f.small, c.calc.title, rr.w - 20), True, GREY), (rr.x + 10, rr.y + 29))
                else:
                    surf.blit(f.uib.render(clip_text(f.uib, "%s - %s" % (c.group, c.label), rr.w - 20), True, ACCENT), (rr.x + 10, rr.y + 8))
                    for k, ln in enumerate(wrap(c.text.replace("\n", " "), f.small, rr.w - 20)[:3]):
                        surf.blit(f.small.render(ln, True, GREY), (rr.x + 10, rr.y + 32 + k * 17))
            else:
                surf.blit(f.uib.render(clip_text(f.uib, c.title, rr.w - 20), True, ACCENT), (rr.x + 10, rr.y + 8))
                for k, ln in enumerate(wrap(c.desc, f.small, rr.w - 20)[:3]):
                    surf.blit(f.small.render(ln, True, GREY), (rr.x + 10, rr.y + 32 + k * 17))
        # header drawn last so scrolled cards go under it
        pygame.draw.rect(surf, HEADER, (0, 0, W, 140))
        bl = draw_header_buttons(surf, f, W, 12)
        surf.blit(f.title.render(clip_text(f.title, "GENG-3130  Engineering Economics Calculator", bl - 40), True, WHITE), (20, 12))
        surf.blit(f.small.render(clip_text(f.small, "Pick a question type (mouse, or arrows + Enter). Assignment and quiz questions are at the bottom. "
                                           "Type to search, e.g. 'bond', 'gradient', 'quiz 4'.", W - 40), True, HDR_SUB), (22, 50))
        sb = pygame.Rect(20, 84, min(W - 40, 560), 38)
        pygame.draw.rect(surf, PANEL, sb, border_radius=6)
        if self.q:
            surf.blit(f.ui.render(self.q, True, INK), (sb.x + 12, sb.y + 8))
        else:
            surf.blit(f.ui.render("Search...", True, PLACE), (sb.x + 12, sb.y + 8))
        if (pygame.time.get_ticks() // 500) % 2 == 0:
            cx = sb.x + 12 + f.ui.size(self.q)[0] + 2
            pygame.draw.line(surf, INK, (cx, sb.y + 8), (cx, sb.y + 30), 2)
        if not rects:
            surf.blit(f.h1.render("No match - clear the search with Backspace.", True, WHITE), (20, 170))
        self._rects = rects
        self.bar.draw(surf, pygame.Rect(W - 16, 148, 10, H_ - 156), self.scroll, maxs)
        return rects

    def _nav(self, d):
        """Move the selection to the nearest card above (d=-1) or below (d=+1)."""
        if not self._rects:
            return
        cur = self._rects[self.sel][1]
        best, bk = None, None
        for n_, (c, r, st) in enumerate(self._rects):
            if d > 0 and r.top <= cur.top + 4:
                continue
            if d < 0 and r.top >= cur.top - 4:
                continue
            key = (abs(r.top - cur.top), abs(r.centerx - cur.centerx))
            if bk is None or key < bk:
                best, bk = n_, key
        if best is not None:
            self.sel = best

    def on_event(self, e):
        s = self.bar.event(e, self.scroll, self.maxs)
        if s is not None:
            self.scroll = s
            self.moved = True
            return None
        n = len(self._rects)
        if e.type == pygame.MOUSEMOTION:
            self.moved = True
        elif e.type == pygame.MOUSEWHEEL:
            self.scroll = max(0, self.scroll - e.y * 60)
            self.moved = True    # mouse mode: don't snap back to the keyboard selection
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for c, r, st in self._rects:
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
            elif e.key in (pygame.K_RIGHT, pygame.K_TAB):
                self.sel = min(n - 1, self.sel + 1) if n else 0
                self.moved = False
            elif e.key == pygame.K_LEFT:
                self.sel = max(0, self.sel - 1)
                self.moved = False
            elif e.key == pygame.K_DOWN:
                self._nav(1)
                self.moved = False
            elif e.key == pygame.K_UP:
                self._nav(-1)
                self.moved = False
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and n:
                return self._rects[self.sel][0]
            elif e.key == pygame.K_PAGEDOWN:
                self.scroll += 400
                self.moved = True
            elif e.key == pygame.K_PAGEUP:
                self.scroll -= 400
                self.moved = True
        return None


def main():
    pygame.init()
    pygame.display.set_caption("GENG-3130 Engineering Economics Calculator")
    pygame.display.set_mode((1360, 840), pygame.RESIZABLE)
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
            if e.type == pygame.KEYDOWN and (e.mod & pygame.KMOD_CTRL):
                if e.key == pygame.K_d:
                    toggle_dark()
                    continue
                if e.key == pygame.K_t:
                    toggle_table(screens)
                    continue
                if e.key == pygame.K_k and current is not None and current.c.contbtn:
                    current.toggle_cont()
                    continue
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                hit = next((nm for nm, r in HDR_BTN.items() if r.collidepoint(e.pos)), None)
                if hit == "dark":
                    toggle_dark()
                    continue
                if hit == "table":
                    toggle_table(screens)
                    continue
                if hit == "cont" and current is not None:
                    current.toggle_cont()
                    continue
            if current is None:
                r = menu.on_event(e)
                if r is not None:
                    if r not in screens:
                        screens[r] = CalcScreen(r.calc, fonts, r) if isinstance(r, Question) else CalcScreen(r, fonts)
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
