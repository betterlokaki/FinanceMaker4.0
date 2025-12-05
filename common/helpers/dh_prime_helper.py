"""DH prime extraction helper for IBKR OAuth."""
import re
import subprocess
from pathlib import Path


def extract_dh_prime(dh_param_path: Path | str) -> str:
    """Extract DH prime value from DH param file using OpenSSL.
    
    Args:
        dh_param_path: Path to the DH parameter file.
        
    Returns:
        Extracted DH prime as hex string.
        
    Raises:
        ValueError: If prime cannot be extracted.
        FileNotFoundError: If DH param file doesn't exist.
    """
    path = Path(dh_param_path)
    if not path.exists():
        raise FileNotFoundError(f"DH param file not found: {path}")
    
    result = subprocess.run(
        ["openssl", "dhparam", "-in", str(path), "-text"],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        raise ValueError(f"OpenSSL failed: {result.stderr}")
    
    match = re.search(
        r"(?:prime|P):\s*((?:\s*[0-9a-fA-F:]+\s*)+)",
        result.stdout
    )
    
    if not match:
        raise ValueError("Could not extract DH prime from file")
    
    dh_prime = re.sub(r"[\s:]", "", match.group(1))
    return dh_prime
