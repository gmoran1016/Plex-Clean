# Dependency Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade every declared application dependency, Docker build runtime, and GitHub Actions dependency to its latest stable release, then verify and publish the result.

**Architecture:** Keep the existing React/Vite frontend, FastAPI backend, multi-stage Docker image, and single GitHub Actions publishing workflow. Refresh the existing manifests and lockfile without changing application behavior or adding runtime libraries.

**Tech Stack:** React 19.3.1, TypeScript 7.0.2 compiler with the current TypeScript 6 API compatibility package for tests, Vite 8.3.5, Python 3.15, FastAPI, Docker, GitHub Actions.

**Spec:** User request in this task; preserve product and security constraints in `PRODUCT.md`.

## Global Constraints

- Preserve Plex-mediated deletion, server-side credentials, and the absence of media mounts.
- Keep `requires-python` compatible with Python 3.12 and newer.
- Upgrade build and runtime Python requirements to setuptools 84.2.1, FastAPI 0.156.8, HTTPX 0.28.1, cryptography 50.0.2, and Uvicorn 0.54.0.
- Upgrade the npm lockfile and the GitHub Actions dependencies alongside their manifests.
- Verify frontend tests and build, backend tests, Docker image build, and the pushed GitHub Actions result.

## Review Focus

- React 19 and its types may expose JSX or component typing changes; catch with the TypeScript/Vite build.
- TypeScript 7 has no programmatic compiler API and stricter side-effect import checks; keep the existing test API on the current TypeScript 6 compatibility package and include Vite's CSS declarations.
- Latest Python packages must resolve and import on Python 3.15; catch with backend tests and the Docker build.
- Updated Docker base tags must exist for the target platforms; catch with the image build.
- Updated GitHub Action major versions must remain compatible with the publishing workflow; catch with the GitHub Actions run after push.

---

### Task 1: Refresh frontend dependencies

**Files:** `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/vite-env.d.ts`

- [x] Update React and React DOM to 19.3.1; React type packages to 19.3.0; plugin-react to 6.1.2; the TypeScript 7.0.2 compiler plus current TypeScript 6 API compatibility package; and Vite to 8.3.5.
- [x] Reference `vite/client` types so the CSS side-effect import is declared to TypeScript 7.
- [x] Regenerate the lockfile with npm so the full dependency tree uses current resolutions.
- [x] Run `npm test` and `npm run build` from `frontend/`; both must pass.

### Task 2: Refresh Python, container, and workflow dependencies

**Files:** `pyproject.toml`, `Dockerfile`, `.github/workflows/publish-container.yml`

- [x] Raise build and runtime Python minimums to the versions listed above while retaining Python `>=3.12` support.
- [x] Move the build image to Node 26 Alpine and the runtime image to Python 3.15 slim.
- [x] Update actions/checkout to v7.0.1, setup-buildx to v4.4.1, login to v4.6.0, metadata to v6.2.0, and build-push to v7.4.0.
- [x] Run backend tests and build the Docker image; the image must start and serve its health endpoint.

### Task 3: Publish and verify

**Files:** all changes above

- [ ] Commit only the dependency refresh, push `master`, and identify the workflow run for that commit.
- [ ] Wait for the GitHub Actions build-and-publish workflow to finish successfully.
