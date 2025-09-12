from fr24_tools import live_flights
from .natural_language import parse_query
from .bot import answer


if __name__ == "__main__":
    while True:
        q = input("You:")
        if not q or q.lower() in {"exit","quit"}: break
        print(answer(q), "\n")

