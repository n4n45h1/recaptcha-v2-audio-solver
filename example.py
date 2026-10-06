"""使い方の例。"""
from recaptcha_solver import RecaptchaSolver

solver = RecaptchaSolver(headless=True, timeout=120)

# テスト許可のあるページを解いてtoken取得
token = solver.solve_with_retries(
    "https://www.google.com/recaptcha/api2/demo",
    "6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-",
    retries=2,
)
print("token len:", len(token))

# g-recaptcha-response として送る。tokenの有効期限は約2分。
