from scipy.stats import uniform, lognorm, gamma, norm, expon
import pandas as pd
import numpy as np
import math


def fix_arrival_distribution(arrival_distribution: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate median values for the arrival distribution.

    For each row in arrival_distribution:
      - If distribution_name is 'fix', the median_value is the same as parameter_1.
      - Otherwise, use u=0.5 to compute the median via inverse CDF.

    Distribution parameter mapping:
      - uniform:  parameter_1 = minimum,      parameter_2 = maximum
      - lognorm:  parameter_1 = mean,         parameter_2 = variance,
                  parameter_3 = minimum,      parameter_4 = maximum
      - gamma:    parameter_1 = mean,         parameter_2 = variance,
                  parameter_3 = minimum,      parameter_4 = maximum
      - norm:     parameter_1 = mean,         parameter_2 = std,
                  parameter_3 = minimum,      parameter_4 = maximum
      - expon:    parameter_1 = mean,         parameter_2 = minimum,
                  parameter_3 = maximum

    Parameters
    ----------
    arrival_distribution : pd.DataFrame
        Arrival distribution table with columns including
        ['name', 'distribution_name', 'parameter_1', ..., 'parameter_4'].

    Returns
    -------
    pd.DataFrame
        DataFrame with columns ['name', 'median_value'].
    """

    if arrival_distribution.empty:
        return pd.DataFrame(columns=["name", "median_value"])

    # Helper: truncated ppf
    def trunc_ppf(u, dist, low, high):
        if low is None and high is None:
            return dist.ppf(u)
        a = dist.cdf(low) if low is not None else 0.0
        b = dist.cdf(high) if high is not None else 1.0
        # guard
        eps = np.finfo(float).eps
        a = min(max(a, 0.0), 1.0 - eps)
        b = min(max(b, a + eps), 1.0)
        return dist.ppf(a + (b - a) * u)

    # Helper: convert to float or None
    def to_float(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    median_values = []
    u = 0.5  # median

    for _, row in arrival_distribution.iterrows():
        d = str(row["distribution_name"]).lower()

        p1 = to_float(row.get("parameter_1"))
        p2 = to_float(row.get("parameter_2"))
        p3 = to_float(row.get("parameter_3"))
        p4 = to_float(row.get("parameter_4"))

        if d == "fix":
            # Fixed distribution: median is the same as parameter_1
            median_val = p1

        elif d == "uniform":
            # [min, max]
            minimum, maximum = float(p1), float(p2)
            median_val = uniform.ppf(u, loc=minimum, scale=(maximum - minimum))

        elif d == "lognorm":
            # [mean, var, minimum, maximum]
            mean, var = float(p1), float(p2)
            pow_mean = pow(mean, 2)
            phi = math.sqrt(var + pow_mean)
            mu = math.log(pow_mean / phi)
            sigma = math.sqrt(math.log(phi ** 2 / pow_mean))
            base = lognorm(s=sigma, scale=math.exp(mu))
            median_val = trunc_ppf(u, base, p3, p4)

        elif d == "gamma":
            # [mean, var, minimum, maximum]
            mean, var = float(p1), float(p2)
            a = pow(mean, 2) / var
            scale = var / mean
            base = gamma(a=a, scale=scale)
            median_val = trunc_ppf(u, base, p3, p4)

        elif d == "norm":
            # [mean, std, minimum, maximum]
            mean, std = float(p1), float(p2)
            base = norm(loc=mean, scale=std)
            median_val = trunc_ppf(u, base, p3, p4)

        elif d == "expon":
            # [mean, minimum, maximum]  (loc=0)
            mean, minimum = float(p1), float(p2)
            scale = mean - minimum
            if scale < 0.0:
                scale = mean
            base = expon(loc=minimum, scale=scale)
            median_val = trunc_ppf(u, base, p2, p3)

        else:
            # Unknown distribution: use parameter_1 as fallback
            median_val = p1

        median_values.append(median_val)

    result = pd.DataFrame({
        "name": arrival_distribution["name"].values,
        "median_value": median_values,
    })

    return result
