#!/usr/bin/env python3
"""Fail closed when a restrictive profile cannot resolve cron capabilities."""

import sys
from pathlib import Path


MARKER = "OPEN_CLOUD_RESTRICTIVE_CRON_FAIL_CLOSED_V1"


def main():
    path = Path(sys.argv[1])
    source = path.read_text()
    if MARKER in source:
        return
    fallback = """    except Exception as exc:
        logger.warning(
            "Cron toolset resolution failed, falling back to full default toolset: %s",
            exc,
        )
        return None
"""
    replacement = """    except Exception as exc:
        # OPEN_CLOUD_RESTRICTIVE_CRON_FAIL_CLOSED_V1
        if os.environ.get("OPEN_CLOUD_RESTRICTIVE_PROFILE") == "1":
            raise RuntimeError("restrictive task profile tool resolution failed") from exc
        logger.warning(
            "Cron toolset resolution failed, falling back to full default toolset: %s",
            exc,
        )
        return None
"""
    if fallback not in source:
        raise SystemExit("cron toolset fallback region not found")
    config_error = """        except Exception as e:
            logger.warning("Job '%s': failed to load config.yaml, using defaults: %s", job_id, e)
"""
    config_replacement = """        except Exception as e:
            if os.environ.get("OPEN_CLOUD_RESTRICTIVE_PROFILE") == "1":
                raise RuntimeError("restrictive task profile config failed to load") from e
            logger.warning("Job '%s': failed to load config.yaml, using defaults: %s", job_id, e)
"""
    if config_error not in source:
        raise SystemExit("cron config fallback region not found")
    path.write_text(source.replace(fallback, replacement, 1).replace(config_error, config_replacement, 1))


if __name__ == "__main__":
    main()
