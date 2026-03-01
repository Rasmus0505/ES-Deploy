# Alembic migration commands

## Upgrade
alembic -c alembic.ini upgrade head

## Downgrade
alembic -c alembic.ini downgrade -1
