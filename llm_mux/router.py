from __future__ import annotations

from .config import Upstream


class UpstreamRegistry:
    def __init__(self, upstreams: tuple[Upstream, ...]) -> None:
        self._upstreams = {upstream.name: upstream for upstream in upstreams}
        # Prefer the longest prefix so names like "minadioro-ollama" work.
        self._prefixes = sorted(self._upstreams, key=len, reverse=True)

    def list_upstreams(self) -> list[str]:
        return sorted(self._upstreams)

    def get_upstream(self, public_model: str) -> tuple[Upstream, str]:
        for prefix in self._prefixes:
            separator = f"{prefix}-"
            if public_model.startswith(separator):
                upstream_model = public_model[len(separator) :]
                if upstream_model:
                    return self._upstreams[prefix], upstream_model

        raise UnknownModelError(public_model, self.list_upstreams())


class UnknownModelError(ValueError):
    def __init__(self, model: str, available_upstreams: list[str]) -> None:
        super().__init__(f"Unknown model '{model}'")
        self.model = model
        self.available_upstreams = available_upstreams
