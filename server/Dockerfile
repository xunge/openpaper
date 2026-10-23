FROM python:3.12-slim-bookworm

WORKDIR /app

# PyPI is not reachable from every network this stack gets deployed on, so the
# index is a build arg with a domestic default rather than a hardcoded one.
# Override with --build-arg PIP_INDEX_URL=https://pypi.org/simple.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install --index-url "$PIP_INDEX_URL" uv

# Dependencies come from uv.lock rather than from pyproject.toml. The ranges
# there are floors with no ceilings, so resolving at build time meant every
# image picked up whatever had been published that day, and a release from any
# dependency could break production with no change on our side. Upgrades are
# now a deliberate `uv lock --upgrade`, reviewed and committed like any other
# change. --frozen fails the build if the lock has drifted from pyproject.toml.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o requirements.txt \
    && uv pip install --system --index-url "$PIP_INDEX_URL" -r requirements.txt

# App code is copied after the install so editing it doesn't rebuild the
# dependency layer.
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY shell.py ./
COPY gunicorn.config.py ./

CMD ["gunicorn", "-c", "gunicorn.config.py", "app.main:app"]
