from .ui_tk import HUD
from .poller import Poller

def main():
    app = HUD(Poller)
    app.mainloop()

if __name__ == "__main__":
    main()
