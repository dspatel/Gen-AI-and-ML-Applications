from __future__ import annotations

from math import erf, exp, log, sqrt


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _d1_d2(spot: float, strike: float, years_to_expiry: float, risk_free_rate: float, sigma: float) -> tuple[float, float]:
    if spot <= 0 or strike <= 0 or years_to_expiry <= 0 or sigma <= 0:
        raise ValueError("Invalid Black-Scholes inputs")
    sigma_sqrt_t = sigma * sqrt(years_to_expiry)
    d1 = (log(spot / strike) + (risk_free_rate + 0.5 * sigma * sigma) * years_to_expiry) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t
    return d1, d2


def black_scholes_price(
    right: str,
    spot: float,
    strike: float,
    years_to_expiry: float,
    risk_free_rate: float,
    sigma: float,
) -> float:
    d1, d2 = _d1_d2(spot, strike, years_to_expiry, risk_free_rate, sigma)
    discount = exp(-risk_free_rate * years_to_expiry)
    if str(right).lower() == "call":
        return (spot * _norm_cdf(d1)) - (strike * discount * _norm_cdf(d2))
    return (strike * discount * _norm_cdf(-d2)) - (spot * _norm_cdf(-d1))


def implied_volatility(
    right: str,
    premium: float,
    spot: float,
    strike: float,
    years_to_expiry: float,
    risk_free_rate: float,
    max_iterations: int = 80,
    tolerance: float = 1e-5,
) -> float | None:
    if premium <= 0 or spot <= 0 or strike <= 0 or years_to_expiry <= 0:
        return None
    lower = 1e-4
    upper = 6.0
    intrinsic = max(spot - strike, 0.0) if str(right).lower() == "call" else max(strike - spot, 0.0)
    if premium < intrinsic * 0.5:
        return None
    try:
        price_low = black_scholes_price(right, spot, strike, years_to_expiry, risk_free_rate, lower)
        price_high = black_scholes_price(right, spot, strike, years_to_expiry, risk_free_rate, upper)
    except ValueError:
        return None
    if premium < price_low - tolerance or premium > price_high + tolerance:
        return None
    for _ in range(max_iterations):
        sigma = (lower + upper) / 2.0
        try:
            model_price = black_scholes_price(right, spot, strike, years_to_expiry, risk_free_rate, sigma)
        except ValueError:
            return None
        diff = model_price - premium
        if abs(diff) <= tolerance:
            return sigma
        if diff > 0:
            upper = sigma
        else:
            lower = sigma
    return (lower + upper) / 2.0


def option_delta(
    right: str,
    spot: float,
    strike: float,
    years_to_expiry: float,
    risk_free_rate: float,
    sigma: float,
) -> float | None:
    if sigma <= 0 or spot <= 0 or strike <= 0 or years_to_expiry <= 0:
        return None
    try:
        d1, _ = _d1_d2(spot, strike, years_to_expiry, risk_free_rate, sigma)
    except ValueError:
        return None
    if str(right).lower() == "call":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0
