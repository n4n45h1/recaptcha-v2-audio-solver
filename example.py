"""Library usage example."""
from recaptcha_solver import RecaptchaSolver

solver = RecaptchaSolver(headless=True, timeout=120)

# Solve a page you are authorized to test and get the token.
token = solver.solve_with_retries(
    "https://www.google.com/recaptcha/api2/demo",
    "6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-",
    retries=2,
)
print("token len:", len(token))

# Submit it with your form as the `g-recaptcha-response` field
# within ~2 minutes (tokens expire).
