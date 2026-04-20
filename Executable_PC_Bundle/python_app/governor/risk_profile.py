from dataclasses import dataclass, asdict
import json


@dataclass
class RiskProfile:
    mode: str = "NORMAL"           # NORMAL, REDUCED, DEFENSIVE, HALTED
    size_multiplier: float = 1.0   # 0.33=1 contract, 0.67=2, 1.0=3
    killswitch_override: bool = False
    reason: str = ""

    def to_json_line(self) -> str:
        return json.dumps(asdict(self)) + "\n"

    @classmethod
    def offline_fallback(cls) -> "RiskProfile":
        return cls(mode="REDUCED", size_multiplier=0.34, reason="Bridge offline fallback")

    @classmethod
    def from_dict(cls, d: dict) -> "RiskProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
