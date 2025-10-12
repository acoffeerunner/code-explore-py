"""Git operations service for cloning and inspecting repositories."""

import asyncio
import hashlib
import shutil
from pathlib import Path
from uuid import UUID

import structlog

from code_explorer.config import Settings, get_settings
from code_explorer.utils.retry import TransientError, with_async_retry
from code_explorer.utils.security import (
    SSRFError,
    sanitize_branch_name,
    validate_git_url,
)

logger = structlog.get_logger(__name__)


class GitError(Exception):
    """Base exception for Git operations."""

    pass


class RepoNotFoundError(GitError):
    """Repository not found or not accessible."""

    pass


class BranchNotFoundError(GitError):
    """Branch not found in repository."""

    pass


class RepoTooLargeError(GitError):
    """Repository exceeds size limit."""

    pass


class CloneTimeoutError(GitError):
    """Clone operation timed out."""

    pass


class GitService:
    """Service for Git repository operations."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize Git service."""
        self.settings = settings or get_settings()
        self.clone_base_path = self.settings.clone_base_path
        self.clone_timeout = self.settings.git_clone_timeout
        self.max_size_mb = self.settings.max_repo_size_mb

    async def get_branches(self, url: str) -> tuple[str, list[str]]:
        """
        Fetch available branches for a repository.

        Args:
            url: Git repository URL (HTTPS)

        Returns:
            Tuple of (default_branch, list of branch names)

        Raises:
            RepoNotFoundError: If repository is not accessible
            SSRFError: If URL targets internal resources
        """
        # Validate URL for SSRF and host allowlist
        try:
            validated_url = validate_git_url(url, self.settings)
        except SSRFError as e:
            raise GitError(f"URL validation failed: {e}") from e

        log = logger.bind(url=validated_url)
        await log.ainfo("Fetching branches")

        try:
            # Get HEAD reference to find default branch
            head_proc = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                "--symref",
                url,
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            head_stdout, head_stderr = await asyncio.wait_for(
                head_proc.communicate(),
                timeout=30,
            )

            if head_proc.returncode != 0:
                error_msg = head_stderr.decode().strip()
                await log.awarning("Failed to access repository", error=error_msg)
                raise RepoNotFoundError(f"Cannot access repository: {error_msg}")

            # Parse default branch from HEAD symref
            default_branch = "main"  # fallback
            head_output = head_stdout.decode()
            for line in head_output.split("\n"):
                if line.startswith("ref: refs/heads/"):
                    default_branch = line.split("refs/heads/")[1].split()[0]
                    break

            # Get all branches
            branches_proc = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                "--heads",
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            branches_stdout, _ = await asyncio.wait_for(
                branches_proc.communicate(),
                timeout=30,
            )

            branches: list[str] = []
            for line in branches_stdout.decode().split("\n"):
                if "refs/heads/" in line:
                    branch_name = line.split("refs/heads/")[1].strip()
                    if branch_name:
                        branches.append(branch_name)

            await log.ainfo(
                "Fetched branches",
                default_branch=default_branch,
                branch_count=len(branches),
            )
            return default_branch, sorted(branches)

        except asyncio.TimeoutError:
            await log.awarning("Timeout fetching branches")
            raise TransientError("Timeout fetching repository branches")

    async def validate_branch(self, url: str, branch: str) -> bool:
        """
        Validate that a branch exists in the repository.

        Args:
            url: Git repository URL
            branch: Branch name to validate

        Returns:
            True if branch exists

        Raises:
            BranchNotFoundError: If branch doesn't exist
        """
        _, branches = await self.get_branches(url)
        if branch not in branches:
            raise BranchNotFoundError(f"Branch '{branch}' not found in repository")
        return True

    @with_async_retry(max_attempts=2, exceptions=(TransientError,))
    async def clone_repo(
        self,
        url: str,
        branch: str,
        repo_id: UUID,
    ) -> tuple[Path, str]:
        """
        Clone a repository to local filesystem.

        Args:
            url: Git repository URL (HTTPS)
            branch: Branch to clone
            repo_id: Repository UUID for directory naming

        Returns:
            Tuple of (clone_path, commit_hash)

        Raises:
            RepoNotFoundError: If repository is not accessible
            RepoTooLargeError: If repository exceeds size limit
            CloneTimeoutError: If clone times out
            GitError: If URL or branch validation fails
        """
        # Validate URL and branch for security
        try:
            validated_url = validate_git_url(url, self.settings)
            validated_branch = sanitize_branch_name(branch)
        except Exception as e:
            raise GitError(f"Validation failed: {e}") from e

        log = logger.bind(url=validated_url, branch=validated_branch, repo_id=str(repo_id))
        await log.ainfo("Starting clone")

        # Create unique clone directory
        clone_path = self.clone_base_path / str(repo_id)

        # Clean up existing clone if any
        if clone_path.exists():
            await log.ainfo("Cleaning up existing clone directory")
            shutil.rmtree(clone_path)

        clone_path.mkdir(parents=True, exist_ok=True)

        try:
            # Shallow clone with depth 1
            proc = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                validated_branch,
                "--single-branch",
                validated_url,
                str(clone_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            _, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.clone_timeout,
            )

            if proc.returncode != 0:
                error_msg = stderr.decode().strip()
                await log.awarning("Clone failed", error=error_msg)
                if "not found" in error_msg.lower():
                    raise RepoNotFoundError(f"Repository or branch not found: {error_msg}")
                raise GitError(f"Clone failed: {error_msg}")

            # Check repository size post-clone
            size_mb = await self._get_directory_size_mb(clone_path)
            await log.ainfo("Clone completed", size_mb=size_mb)

            if size_mb > self.max_size_mb:
                await log.awarning(
                    "Repository exceeds size limit",
                    size_mb=size_mb,
                    limit_mb=self.max_size_mb,
                )
                shutil.rmtree(clone_path)
                raise RepoTooLargeError(
                    f"Repository size ({size_mb:.1f}MB) exceeds limit ({self.max_size_mb}MB)"
                )

            # Get commit hash
            commit_hash = await self._get_commit_hash(clone_path)
            await log.ainfo("Got commit hash", commit_hash=commit_hash)

            return clone_path, commit_hash

        except asyncio.TimeoutError:
            await log.awarning("Clone timed out")
            if clone_path.exists():
                shutil.rmtree(clone_path)
            raise CloneTimeoutError(
                f"Clone timed out after {self.clone_timeout} seconds"
            )

    async def _get_directory_size_mb(self, path: Path) -> float:
        """Get directory size in megabytes using du."""
        proc = await asyncio.create_subprocess_exec(
            "du",
            "-sm",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode == 0:
            size_str = stdout.decode().split()[0]
            return float(size_str)
        return 0.0

    async def _get_commit_hash(self, clone_path: Path) -> str:
        """Get the current commit hash from cloned repo."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(clone_path),
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode == 0:
            return stdout.decode().strip()
        raise GitError("Failed to get commit hash")

    def cleanup_clone(self, repo_id: UUID) -> None:
        """
        Clean up cloned repository directory.

        Args:
            repo_id: Repository UUID
        """
        clone_path = self.clone_base_path / str(repo_id)
        if clone_path.exists():
            shutil.rmtree(clone_path)
            logger.info("Cleaned up clone directory", repo_id=str(repo_id))

    def get_clone_path(self, repo_id: UUID) -> Path:
        """Get the clone path for a repository."""
        return self.clone_base_path / str(repo_id)


def get_content_hash(content: str) -> str:
    """Generate SHA256 hash of content."""
    return hashlib.sha256(content.encode()).hexdigest()
