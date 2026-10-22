"""CLI. Inside the container: python -m icarus_manager <command>

  serve                 run the manager (default)
  backup                backup now (even without changes)
  status                server status
  restart [--update]    graceful restart, refuses while players are online unless --force
  update                update the game now (if there is a new build)
  restore <file>        restore a backup from ./backups (.zip or the .001 of a split one)
  test-discord          send a test message to the webhook
  test-rclone           check the rclone destination
"""

import argparse
import logging
import sys

from .config import Config, env_str
from .service import Manager


def main():
    parser = argparse.ArgumentParser(prog="icarus_manager", description="Gestão do servidor de Icarus")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve")
    sub.add_parser("backup")
    sub.add_parser("status")
    p = sub.add_parser("restart")
    p.add_argument("--update", action="store_true", help="atualiza o jogo antes de arrancar")
    p.add_argument("--force", action="store_true", help="mesmo com jogadores online")
    p = sub.add_parser("update")
    p.add_argument("--force", action="store_true", help="mesmo com jogadores online / sem versão nova")
    p = sub.add_parser("restore")
    p.add_argument("file", help="nome do ficheiro em ./backups")
    p.add_argument("--force", action="store_true", help="mesmo com jogadores online")
    sub.add_parser("test-discord")
    sub.add_parser("test-rclone")
    args = parser.parse_args()

    logging.basicConfig(level=env_str("LOG_LEVEL", "INFO").upper(),
                        format="[manager] %(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("discord").setLevel(logging.WARNING)
    manager = Manager(Config.from_env())
    command = args.command or "serve"

    if command == "serve":
        manager.serve()
    elif command == "backup":
        print(manager.manual_backup())
    elif command == "status":
        print(manager.status_text())
    elif command == "restart":
        finish(*manager.restart("pedido na linha de comandos", update=args.update, force=args.force))
    elif command == "update":
        finish(*manager.update_now(force=args.force))
    elif command == "restore":
        finish(*manager.restore(args.file, force=args.force))
    elif command == "test-discord":
        if not manager.discord.enabled:
            sys.exit("DISCORD_WEBHOOK_URL não está definido")
        manager.discord.send("✅ Teste do webhook do servidor de Icarus: está a funcionar!")
        print("Mensagem enviada.")
    elif command == "test-rclone":
        if not manager.rclone.enabled:
            sys.exit(f"rclone {manager.rclone.describe()}")
        manager.rclone.check()
        print(f"rclone OK: {manager.rclone.remote}")


def finish(ok, message):
    print(("OK: " if ok else "ERRO: ") + message)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
