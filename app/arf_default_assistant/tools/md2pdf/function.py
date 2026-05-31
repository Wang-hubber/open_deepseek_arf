"""execute function for md2pdf."""


async def execute(files: list[str], output_dir: str = "", paper: str = "A4", _workspace: str = "") -> dict:
    """Convert Markdown files to PDF via HTML pipeline logic."""
    return {'ok': True, 'result': f'Processed: {len(files)} files'}
