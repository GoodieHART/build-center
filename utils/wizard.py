"""
Interactive CLI wizard helper utilities for the Build Center Modal app.

Provides a collection of pure-Python helper functions for building
interactive command-line wizards using the fetch-display-select pattern.
All functions use ``input()`` for user interaction and implement proper
validation loops that re-prompt on invalid input — they never call
``exit()`` or crash on bad input.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, TypeVar

T = TypeVar("T")

# --- Display helpers ---


def print_error(message: str) -> None:
    """Print a formatted error message."""
    print(f"\u274c {message}")


def print_success(message: str) -> None:
    """Print a formatted success message."""
    print(f"\u2705 {message}")


def print_info(message: str) -> None:
    """Print a formatted info message."""
    print(f"\u2139\ufe0f {message}")


def display_options(options: Sequence[str], title: str) -> None:
    """Print a numbered list of options under a title header.

    Args:
        options: Sequence of option strings to display.
        title: Header text shown above the list.
    """
    print(f"=== {title} ===")
    for i, opt in enumerate(options, start=1):
        print(f"{i}. {opt}")


# --- Input helpers ---


def prompt_string(prompt_text: str, default: Optional[str] = None) -> str:
    """Prompt the user for a string value with an optional default.

    If *default* is provided it is shown in brackets in the prompt and
    returned when the user enters an empty string.

    Args:
        prompt_text: The question / label shown to the user.
        default: Optional default value.

    Returns:
        The entered string, or *default* if the input was empty.
    """
    if default is not None:
        full_prompt = f"{prompt_text} [{default}]: "
    else:
        full_prompt = f"{prompt_text}: "

    value = input(full_prompt).strip()
    if not value and default is not None:
        return default
    return value


def select_option(options: Sequence[T], prompt: str) -> T:
    """Present a numbered list and let the user pick by number.

    The user is re-prompted on:
    - Non-numeric input
    - Out-of-range numbers
    - Empty input

    Args:
        options: Sequence of options to choose from (any type).
        prompt: Question text shown after the numbered list.

    Returns:
        The selected option value.
    """
    while True:
        raw = input(f"{prompt} (1-{len(options)}): ").strip()

        if not raw:
            print_error("Input cannot be empty. Please enter a number.")
            continue

        try:
            choice = int(raw)
        except ValueError:
            print_error(f"Invalid input '{raw}'. Please enter a number between 1 and {len(options)}.")
            continue

        if choice < 1 or choice > len(options):
            print_error(
                f"Number out of range. Please enter a number between 1 and {len(options)}."
            )
            continue

        return options[choice - 1]


def confirm_selection(summary_dict: Dict[str, Any]) -> bool:
    """Display a build-config summary and ask the user to confirm.

    Prints each key/value pair on its own line and prompts with
    ``Proceed? (y/n): ``.  Accepts ``y`` / ``Y`` / ``yes`` / ``YES``
    as affirmative and ``n`` / ``N`` / ``no`` / ``NO`` as negative.
    Anything else re-prompts.

    Args:
        summary_dict: Key-value pairs describing the build configuration.

    Returns:
        ``True`` if confirmed, ``False`` if rejected.
    """
    print("\n=== Build Configuration Summary ===")
    for key, value in summary_dict.items():
        print(f"{key}: {value}")

    while True:
        answer = input("Proceed? (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print_error("Please answer 'y' or 'n'.")


# --- Data fetching helper ---


def fetch_options(
    source_callable: Callable[[], List[T]],
    label: str,
) -> List[T]:
    """Fetch a list of options by calling *source_callable*.

    Wraps the call with a user-facing status message and converts
    common network / runtime errors into a clear ``RuntimeError``.

    Args:
        source_callable: A zero-argument callable that returns a list.
        label: A human-readable label for the status message (e.g.
            ``"available platforms"``).

    Returns:
        The list returned by *source_callable*.

    Raises:
        RuntimeError: If *source_callable* raises any exception.
    """
    print_info(f"Fetching {label}...")
    try:
        result = source_callable()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch {label}: {exc}"
        ) from exc

    if not result:
        raise RuntimeError(f"No {label} available.")

    return result
