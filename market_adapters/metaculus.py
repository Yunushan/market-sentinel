from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, UnsupportedFeatureError
from .types import MarketContract, MarketEvent, PriceSnapshot


DEFAULT_METACULUS_BASE_URL = "https://www.metaculus.com/api"


class MetaculusAdapter(MarketAdapter):
    """Metaculus read-only adapter using the official authenticated API."""

    metadata = get_market_metadata("metaculus")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        credential = self.resolve_credential(
            "metaculus_api_token",
            ("METACULUS_API_TOKEN",),
            label="METACULUS_API_TOKEN",
        )
        health.update(
            {
                "api_base_url": self.api_base_url,
                "credential_sources": (
                    [{"name": credential.name, "source": credential.source}] if credential else []
                ),
                "data_access_note": (
                    "Metaculus API data access requires authentication; Community Prediction data is access-limited."
                ),
                "trading_supported": False,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("metaculus_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_METACULUS_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        params: Dict[str, Any] = {"limit": desired}
        if query:
            params["search"] = str(query)
        order_by = self.config.get("metaculus_order_by")
        if order_by:
            params["order_by"] = str(order_by)
        data = self._get("/posts/", params=params)
        posts = self._as_post_list(data)
        return [self._event_from_post(post) for post in posts[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        post = self._get_post(str(event_id or "").strip())
        if not post:
            return []
        post_id = str(post.get("id") or event_id).strip()
        contracts: List[MarketContract] = []
        for question in self._questions_from_post(post):
            contracts.extend(self._contracts_from_question(post_id, post, question))
        return contracts

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        post_id, question_id, outcome, choice_id = self._split_contract_id(contract_id)
        post = self._get_post(post_id)
        question = self._find_question(post, question_id)
        if question is None:
            raise MarketConfigurationError(f"Metaculus post {post_id} did not include question {question_id}.")

        if outcome == "YES":
            value = self._binary_probability(question)
        elif outcome == "NO":
            value = 1.0 - self._binary_probability(question)
        elif outcome == "CHOICE":
            if not choice_id:
                raise MarketConfigurationError("Metaculus choice contract requires a choice id.")
            value = self._choice_probability(question, choice_id)
        elif outcome == "VALUE":
            value = self._numeric_forecast(question)
        else:
            raise MarketConfigurationError("Metaculus contract outcome must be YES, NO, CHOICE, or VALUE.")

        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(post_id, question_id, outcome, choice_id),
            last=value,
            midpoint=value,
            source="metaculus_api",
            raw={"post": dict(post), "question": dict(question)},
        )

    def get_orderbook(self, contract_id: str):
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "Metaculus is a forecasting platform and does not expose a trading orderbook.",
        )

    def _get_post(self, ref: str) -> Optional[Mapping[str, Any]]:
        if not ref:
            return None
        data = self._get(f"/posts/{ref}/")
        return data if isinstance(data, Mapping) else None

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params, headers=self._auth_headers())

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").lstrip("/")
        return f"{self.api_base_url}{clean_path}"

    def _auth_headers(self) -> Dict[str, str]:
        credential = self.resolve_credential(
            "metaculus_api_token",
            ("METACULUS_API_TOKEN",),
            required=True,
            label="METACULUS_API_TOKEN",
        )
        return {"Authorization": f"Token {credential.value}"}

    def _event_from_post(self, post: Mapping[str, Any]) -> MarketEvent:
        post_id = str(post.get("id") or "").strip()
        return MarketEvent(
            market_id=self.market_id,
            event_id=post_id,
            title=self._post_title(post),
            url=self._post_url(post),
            status=self._post_status(post),
            raw=dict(post),
        )

    def _contracts_from_question(
        self,
        post_id: str,
        post: Mapping[str, Any],
        question: Mapping[str, Any],
    ) -> List[MarketContract]:
        question_id = str(question.get("id") or "").strip()
        if not question_id:
            return []
        title = self._question_title(question, fallback=self._post_title(post))
        status = self._question_status(question, fallback=self._post_status(post))
        question_type = self._question_type(question)
        if question_type == "BINARY":
            return [
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(post_id, question_id, "YES"),
                    event_id=post_id,
                    title=f"{title} - Yes",
                    outcome="Yes",
                    url=self._post_url(post),
                    status=status,
                    raw={"post": dict(post), "question": dict(question), "outcome": "YES"},
                ),
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(post_id, question_id, "NO"),
                    event_id=post_id,
                    title=f"{title} - No",
                    outcome="No",
                    url=self._post_url(post),
                    status=status,
                    raw={"post": dict(post), "question": dict(question), "outcome": "NO"},
                ),
            ]
        if question_type == "MULTIPLE_CHOICE":
            contracts: List[MarketContract] = []
            for choice_id, choice_label in self._choices_from_question(question):
                contracts.append(
                    MarketContract(
                        market_id=self.market_id,
                        contract_id=self._contract_id(post_id, question_id, "CHOICE", choice_id),
                        event_id=post_id,
                        title=f"{title} - {choice_label}",
                        outcome=choice_label,
                        url=self._post_url(post),
                        status=status,
                        raw={"post": dict(post), "question": dict(question), "choice_id": choice_id},
                    )
                )
            return contracts

        return [
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(post_id, question_id, "VALUE"),
                event_id=post_id,
                title=title,
                outcome="Forecast value",
                url=self._post_url(post),
                status=status,
                raw={"post": dict(post), "question": dict(question), "outcome": "VALUE"},
            )
        ]

    @staticmethod
    def _as_post_list(data: Any) -> List[Mapping[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, Mapping)]
        if isinstance(data, Mapping):
            posts = data.get("results") or data.get("posts") or data.get("items") or []
            if isinstance(posts, list):
                return [item for item in posts if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _questions_from_post(post: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        questions: List[Mapping[str, Any]] = []
        direct = post.get("question")
        if isinstance(direct, Mapping):
            questions.append(direct)
        raw_questions = post.get("questions")
        if isinstance(raw_questions, list):
            questions.extend(item for item in raw_questions if isinstance(item, Mapping))
        for group_key in ("group_of_questions", "question_group", "group"):
            group = post.get(group_key)
            if not isinstance(group, Mapping):
                continue
            for question_key in ("questions", "subquestions"):
                group_questions = group.get(question_key)
                if isinstance(group_questions, list):
                    questions.extend(item for item in group_questions if isinstance(item, Mapping))
        conditional = post.get("conditional")
        if isinstance(conditional, Mapping):
            for value in conditional.values():
                if isinstance(value, Mapping) and MetaculusAdapter._looks_like_question(value):
                    questions.append(value)

        deduped: Dict[str, Mapping[str, Any]] = {}
        for question in questions:
            question_id = str(question.get("id") or "").strip()
            if question_id:
                deduped[question_id] = question
        return list(deduped.values())

    @staticmethod
    def _find_question(post: Optional[Mapping[str, Any]], question_id: str) -> Optional[Mapping[str, Any]]:
        if not post:
            return None
        for question in MetaculusAdapter._questions_from_post(post):
            if str(question.get("id") or "").strip() == question_id:
                return question
        return None

    @staticmethod
    def _looks_like_question(value: Mapping[str, Any]) -> bool:
        return bool(value.get("id")) and any(
            key in value for key in ("type", "question_type", "forecast_type", "possibilities", "aggregations")
        )

    @staticmethod
    def _question_type(question: Mapping[str, Any]) -> str:
        raw = (
            question.get("type")
            or question.get("question_type")
            or question.get("forecast_type")
            or question.get("outcome_type")
        )
        if not raw and isinstance(question.get("possibilities"), Mapping):
            raw = question["possibilities"].get("type")
        normalized = str(raw or "").replace("-", "_").replace(" ", "_").upper()
        if normalized in {"BINARY", "BIN"}:
            return "BINARY"
        if normalized in {"MULTIPLE_CHOICE", "MULTIPLECHOICE", "CHOICE"}:
            return "MULTIPLE_CHOICE"
        if MetaculusAdapter._choices_from_question(question):
            return "MULTIPLE_CHOICE"
        return "VALUE"

    @staticmethod
    def _choices_from_question(question: Mapping[str, Any]) -> List[Tuple[str, str]]:
        raw_choices: Any = question.get("choices") or question.get("options")
        possibilities = question.get("possibilities")
        if isinstance(possibilities, Mapping):
            raw_choices = raw_choices or possibilities.get("choices") or possibilities.get("options")

        choices: List[Tuple[str, str]] = []
        if isinstance(raw_choices, Mapping):
            raw_choices = [
                {"id": key, "label": value}
                for key, value in raw_choices.items()
            ]
        if not isinstance(raw_choices, list):
            return choices
        for index, raw in enumerate(raw_choices):
            if isinstance(raw, Mapping):
                choice_id = str(raw.get("id") or raw.get("key") or index).strip()
                label = str(raw.get("label") or raw.get("name") or raw.get("text") or choice_id).strip()
            else:
                choice_id = str(index)
                label = str(raw)
            if choice_id:
                choices.append((choice_id, label or choice_id))
        return choices

    @staticmethod
    def _binary_probability(question: Mapping[str, Any]) -> float:
        probability = MetaculusAdapter._probability_from_value(question.get("community_prediction"))
        if probability is None:
            probability = MetaculusAdapter._probability_from_value(question.get("communityPrediction"))
        if probability is None:
            probability = MetaculusAdapter._probability_from_value(question.get("probability"))
        if probability is None:
            probability = MetaculusAdapter._probability_from_value(question.get("cp"))
        if probability is None:
            probability = MetaculusAdapter._probability_from_aggregation(question)
        if probability is None:
            raise MarketConfigurationError(
                "Metaculus response did not include an accessible Community Prediction for this question."
            )
        return probability

    @staticmethod
    def _choice_probability(question: Mapping[str, Any], choice_id: str) -> float:
        for raw_map in (
            question.get("choice_probabilities"),
            question.get("choiceProbabilities"),
            question.get("answerProbs"),
        ):
            if isinstance(raw_map, Mapping) and choice_id in raw_map:
                probability = MetaculusAdapter._probability_from_value(raw_map.get(choice_id))
                if probability is not None:
                    return probability
        for raw_choice_id, _label in MetaculusAdapter._choices_from_question(question):
            if raw_choice_id != choice_id:
                continue
            choices = question.get("choices") or question.get("options")
            possibilities = question.get("possibilities")
            if isinstance(possibilities, Mapping):
                choices = choices or possibilities.get("choices") or possibilities.get("options")
            if isinstance(choices, list):
                for raw in choices:
                    if isinstance(raw, Mapping) and str(raw.get("id") or raw.get("key") or "") == choice_id:
                        probability = MetaculusAdapter._probability_from_value(
                            raw.get("probability") or raw.get("community_prediction")
                        )
                        if probability is not None:
                            return probability
        aggregation = MetaculusAdapter._latest_aggregation(question)
        if aggregation:
            values = aggregation.get("forecast_values") or aggregation.get("choice_probabilities")
            if isinstance(values, Mapping) and choice_id in values:
                probability = MetaculusAdapter._probability_from_value(values.get(choice_id))
                if probability is not None:
                    return probability
        raise MarketConfigurationError(
            f"Metaculus response did not include an accessible Community Prediction for choice {choice_id}."
        )

    @staticmethod
    def _numeric_forecast(question: Mapping[str, Any]) -> float:
        for key in ("median", "community_prediction", "communityPrediction", "prediction"):
            value = MetaculusAdapter._number_from_value(question.get(key))
            if value is not None:
                return value
        aggregation = MetaculusAdapter._latest_aggregation(question)
        if aggregation:
            for key in ("center", "median", "q2"):
                value = MetaculusAdapter._number_from_value(aggregation.get(key))
                if value is not None:
                    return value
            centers = aggregation.get("centers")
            if isinstance(centers, list) and centers:
                value = MetaculusAdapter._number_from_value(centers[len(centers) // 2])
                if value is not None:
                    return value
        raise MarketConfigurationError("Metaculus response did not include an accessible numeric forecast.")

    @staticmethod
    def _probability_from_aggregation(question: Mapping[str, Any]) -> Optional[float]:
        aggregation = MetaculusAdapter._latest_aggregation(question)
        if not aggregation:
            return None
        for key in ("prob", "probability", "center", "median", "q2"):
            probability = MetaculusAdapter._probability_from_value(aggregation.get(key))
            if probability is not None:
                return probability
        for key in ("centers", "forecast_values"):
            values = aggregation.get(key)
            if isinstance(values, list) and values:
                candidate = values[-1] if len(values) == 2 else values[0]
                probability = MetaculusAdapter._probability_from_value(candidate)
                if probability is not None:
                    return probability
        return None

    @staticmethod
    def _latest_aggregation(question: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        aggregations = question.get("aggregations")
        if not isinstance(aggregations, Mapping):
            return None
        for key in ("recency_weighted", "community", "unweighted", "metaculus_prediction"):
            aggregation = aggregations.get(key)
            if not isinstance(aggregation, Mapping):
                continue
            latest = aggregation.get("latest")
            if isinstance(latest, Mapping):
                return latest
            if isinstance(aggregation.get("history"), list) and aggregation["history"]:
                last = aggregation["history"][-1]
                if isinstance(last, Mapping):
                    return last
        return None

    @staticmethod
    def _probability_from_value(value: Any) -> Optional[float]:
        if isinstance(value, Mapping):
            for key in ("prob", "probability", "center", "median", "q2", "value"):
                probability = MetaculusAdapter._probability_from_value(value.get(key))
                if probability is not None:
                    return probability
            full = value.get("full")
            if isinstance(full, Mapping):
                return MetaculusAdapter._probability_from_value(full)
            return None
        number = MetaculusAdapter._number_from_value(value)
        if number is None or number < 0.0 or number > 1.0:
            return None
        return number

    @staticmethod
    def _number_from_value(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str, str, Optional[str]]:
        raw = str(contract_id or "").strip()
        parts = raw.split(":")
        if len(parts) < 3:
            raise MarketConfigurationError("Metaculus contract id must be post_id:question_id:outcome.")
        post_id = parts[0].strip()
        question_id = parts[1].strip()
        outcome = parts[2].strip().upper()
        choice_id = parts[3].strip() if len(parts) > 3 else None
        if not post_id or not question_id:
            raise MarketConfigurationError("Metaculus contract id requires post and question ids.")
        if outcome not in {"YES", "NO", "CHOICE", "VALUE"}:
            raise MarketConfigurationError("Metaculus contract outcome must be YES, NO, CHOICE, or VALUE.")
        if outcome == "CHOICE" and not choice_id:
            raise MarketConfigurationError("Metaculus choice contract requires a choice id.")
        return post_id, question_id, outcome, choice_id

    @staticmethod
    def _contract_id(post_id: str, question_id: str, outcome: str, choice_id: Optional[str] = None) -> str:
        if outcome.upper() == "CHOICE":
            return f"{post_id}:{question_id}:CHOICE:{choice_id}"
        return f"{post_id}:{question_id}:{outcome.upper()}"

    @staticmethod
    def _post_title(post: Mapping[str, Any]) -> str:
        question = post.get("question")
        return str(
            post.get("title")
            or (question.get("title") if isinstance(question, Mapping) else "")
            or (question.get("question") if isinstance(question, Mapping) else "")
            or post.get("short_title")
            or post.get("id")
            or ""
        )

    @staticmethod
    def _question_title(question: Mapping[str, Any], *, fallback: str = "") -> str:
        return str(question.get("title") or question.get("question") or question.get("name") or fallback)

    @staticmethod
    def _post_url(post: Mapping[str, Any]) -> str:
        url = str(post.get("url") or post.get("page_url") or "")
        if url.startswith("http"):
            return url
        if url:
            return f"https://www.metaculus.com{url if url.startswith('/') else '/' + url}"
        post_id = str(post.get("id") or "").strip()
        return f"https://www.metaculus.com/questions/{post_id}/" if post_id else "https://www.metaculus.com"

    @staticmethod
    def _post_status(post: Mapping[str, Any]) -> str:
        if post.get("is_resolved") is True or post.get("resolved") is True:
            return "resolved"
        if post.get("closed") is True:
            return "closed"
        return str(post.get("status") or "open").lower()

    @staticmethod
    def _question_status(question: Mapping[str, Any], *, fallback: str = "open") -> str:
        if question.get("resolution") not in (None, "", "open"):
            return "resolved"
        if question.get("resolved") is True:
            return "resolved"
        if question.get("closed") is True:
            return "closed"
        return str(question.get("status") or fallback or "open").lower()
