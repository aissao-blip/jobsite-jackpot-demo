"""CLI: wipe and restore the fictional Jobsite Jackpot demo data.

    python reset_demo.py
"""
from seed_demo import reset_demo

if __name__ == "__main__":
    n = reset_demo()
    print(f"Demo reset complete — {n} fictional creators seeded.")
