set quiet

# List available recipes
default:
    @just --list

# Run unit tests
[group('dev')]
test *args:
    uv run pytest {{ args }}

# Type check
[group('dev')]
check:
    uv run pyright gravier/

# Lint
[group('dev')]
lint:
    uv run ruff check gravier/ tests/

# Format
[group('dev')]
fmt:
    uv run ruff format gravier/ tests/

# Fix lint issues where possible
[group('dev')]
fix:
    uv run ruff check --fix gravier/ tests/
    uv run ruff format gravier/ tests/

# Lint + type check + test
[group('dev')]
ci: lint check test

# Bump version (commitizen — updates pyproject.toml and CHANGELOG)
[group('release')]
bump *args:
    uv run --group bump cz bump {{ args }}
