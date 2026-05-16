# Changelog

## Unreleased

- terminate instances in `sample_cleanup` even when `interrupted=True`, so
  setup-script failures and Ctrl-C don't leave instances running
- remove --fail-with-body from curl to support older versions