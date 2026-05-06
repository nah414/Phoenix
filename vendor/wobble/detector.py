"""
Dr. Frank & Eddy v6.0.0 -- Wobble Detection Layer

Detects divergence among multiple LLM responses using embedding similarity.
Primary: sentence-transformers. Fallback: sklearn TF-IDF cosine distance.

Phase 3B: detect_with_finding() returns a structured DisagreementFinding
instead of collapsing the distance matrix to a scalar. The old detect()
method is preserved for backward compatibility.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer
    HAS_SBERT = True
except ImportError:
    HAS_SBERT = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_distances as sklearn_cosine_distances
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


@dataclass
class WobbleResult:
    score: float
    consensus: bool
    divergent_pairs: List[Tuple[int, int]]
    pairwise_distances: np.ndarray
    n_responses: int
    method: str


@dataclass
class BidirectionalWobbleResult:
    """v4.3: Result of bidirectional wobble verification (GoZ Technique 6).

    Runs BOTH sentence-transformers AND TF-IDF, compares divergent pair
    RANKINGS for self-consistency. If the measurement is trustworthy,
    the ranking of divergent pairs should be consistent across methods
    (isometric matching condition: c_0 ~ 1).
    """
    primary: WobbleResult               # sentence-transformers result
    secondary: WobbleResult              # TF-IDF result
    ranking_agreement: float             # Spearman rank correlation (-1 to 1)
    self_consistent: bool                # ranking_agreement > 0.6
    meta_confidence: float               # 0-1 confidence in measurement
    anomaly_type: Optional[str]          # "paraphrase", "mimicry", or None


@dataclass
class FactorIsolationResult:
    """v4.3: Leave-one-out divergence factor isolation (GoZ Technique 1).

    For N co-authors, run N passes with one excluded each time.
    The co-author whose exclusion produces the largest wobble DROP
    is the divergence factor -- analogous to the theta_3 -> theta_2 swap
    in the GoZ paper's PPP/APP structural decomposition.
    """
    full_wobble: float                      # wobble with all included
    leave_one_out_scores: List[float]       # wobble with each excluded
    divergence_factor_index: int            # which co-author is the theta_2
    wobble_drop: float                      # how much wobble decreased
    drop_ratio: float                       # wobble_drop / full_wobble
    single_factor_divergence: bool          # drop_ratio > 0.6
    factor_label: Optional[str]             # co-author name/model if available


class WobbleDetector:
    """Detect divergence among LLM responses via embedding distance.

    Uses sentence-transformers for high-quality embeddings when available,
    falling back to TF-IDF cosine distance from sklearn.
    """

    DEFAULT_THRESHOLD: float = 0.3
    TFIDF_THRESHOLD_SCALE: float = 2.5  # TF-IDF distances run ~2-3x higher than semantic

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        threshold: float = 0.3,
    ):
        self._model_name = model_name
        self._threshold = threshold
        self._sbert_model: Optional[object] = None

    def detect(self, responses: List[str]) -> WobbleResult:
        """Compute pairwise embedding distances across responses.

        Works with 1-5 responses. With a single response, returns
        a trivial result (score=0, consensus=True, no divergence
        measurable). The sigma formula compensates -- single-model
        results naturally score lower due to reduced verification.

        Args:
            responses: List of LLM response strings (minimum 1).

        Returns:
            WobbleResult with score, divergent pairs, and consensus flag.

        Raises:
            ValueError: If no responses provided.
            RuntimeError: If neither sentence-transformers nor sklearn available.
        """
        if len(responses) < 1:
            raise ValueError("Wobble detection requires at least 1 response")

        # Single response: no comparison possible, consensus by default
        if len(responses) == 1:
            return WobbleResult(
                score=0.0,
                consensus=True,
                divergent_pairs=[],
                pairwise_distances=np.array([[0.0]]),
                n_responses=1,
                method="single_response",
            )

        if HAS_SBERT:
            embeddings = self._embed_sentence_transformers(responses)
            method = "sentence_transformers"
        elif HAS_SKLEARN:
            embeddings = self._embed_tfidf(responses)
            method = "tfidf_fallback"
        else:
            raise RuntimeError(
                "Wobble detection requires sentence-transformers or scikit-learn"
            )

        distances = self._cosine_distance_matrix(embeddings)
        n = len(responses)

        # TF-IDF measures lexical overlap, not semantic similarity.
        # Semantically identical sentences with different words produce high
        # TF-IDF distances (~0.6-0.9) vs low semantic distances (~0.1-0.2).
        # Scale the threshold up for TF-IDF to compensate.
        effective_threshold = self._threshold
        if method == "tfidf_fallback":
            effective_threshold = self._threshold * self.TFIDF_THRESHOLD_SCALE

        # Score = mean of upper-triangle (off-diagonal) distances
        upper_tri = distances[np.triu_indices(n, k=1)]
        score = float(np.mean(upper_tri)) if len(upper_tri) > 0 else 0.0

        # Flag divergent pairs
        divergent_pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                if distances[i, j] > effective_threshold:
                    divergent_pairs.append((i, j))

        consensus = score < effective_threshold

        log.debug(
            "Wobble: score=%.4f consensus=%s method=%s divergent=%d",
            score, consensus, method, len(divergent_pairs),
        )

        return WobbleResult(
            score=score,
            consensus=consensus,
            divergent_pairs=divergent_pairs,
            pairwise_distances=distances,
            n_responses=n,
            method=method,
        )

    def detect_with_finding(
        self,
        responses: List[str],
        query_text: str = "",
        system_prompt_version: str = "v6.0",
        co_author_names: Optional[List[str]] = None,
        co_author_models: Optional[dict] = None,
        co_author_temperatures: Optional[dict] = None,
        co_author_latencies: Optional[List[float]] = None,
        co_author_token_counts: Optional[List[int]] = None,
    ) -> "DisagreementFinding":
        """Compute wobble and return a structured DisagreementFinding.

        This is the Phase 3B replacement for the scalar collapse. It computes
        the same distance matrix as detect(), but returns the FULL structure
        instead of averaging it to a number.

        The disagreement_type is set to UNKNOWN -- the classifier (Phase 3C)
        assigns the real type.

        Args:
            responses: List of co-author response strings.
            query_text: The original query (for Frame).
            system_prompt_version: System prompt version (for Frame).
            co_author_names: Name per response (e.g., ["claude", "gemini", ...]).
            co_author_models: {name: model_version} dict.
            co_author_temperatures: {name: temperature} dict.
            co_author_latencies: Latency in ms per response.
            co_author_token_counts: Token count per response.

        Returns:
            DisagreementFinding with type=UNKNOWN (awaiting classification).
        """
        from wobble.disagreement_types import (
            DisagreementType, SuggestedAction, Frame,
            ProvenanceEntry, DisagreementFinding,
        )

        # Run the same detection logic as detect()
        scalar_result = self.detect(responses)

        # Build Frame from caller context
        names = co_author_names or [f"coauthor_{i}" for i in range(len(responses))]
        models = co_author_models or {n: "unknown" for n in names}
        temps = co_author_temperatures or {n: 0.7 for n in names}

        frame = Frame(
            system_prompt_version=system_prompt_version,
            query_text=query_text,
            query_timestamp=datetime.now(timezone.utc).isoformat(),
            co_author_models=models,
            temperature=temps,
        )

        # Build provenance chain
        latencies = co_author_latencies or [0.0] * len(responses)
        token_counts = co_author_token_counts or [0] * len(responses)
        provenance = []
        for i, text in enumerate(responses):
            name = names[i] if i < len(names) else f"coauthor_{i}"
            provenance.append(ProvenanceEntry(
                co_author_name=name,
                response_text=text,
                latency_ms=latencies[i] if i < len(latencies) else 0.0,
                token_count=token_counts[i] if i < len(token_counts) else 0,
                model_version=models.get(name, "unknown"),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))

        return DisagreementFinding(
            disagreement_type=DisagreementType.UNKNOWN,
            classifier_confidence=0.0,
            classifier_rationale="Awaiting classification",
            classifier_evidence=[],
            distance_matrix=scalar_result.pairwise_distances,
            frame=frame,
            provenance=provenance,
            suggested_action=SuggestedAction.SURFACE_RAW,
            raw_wobble_score=scalar_result.score,
            metadata={
                "consensus": scalar_result.consensus,
                "divergent_pairs": scalar_result.divergent_pairs,
                "n_responses": scalar_result.n_responses,
                "method": scalar_result.method,
            },
        )

    def detect_bidirectional(self, responses: List[str]) -> BidirectionalWobbleResult:
        """v4.3: Run BOTH semantic AND lexical wobble, compare rankings.

        The isometric matching condition (GoZ Technique 6): if the wobble
        measurement is trustworthy, the RANKING of pairwise distances should
        be consistent across both methods (Spearman rank correlation > 0.6).

        Anomaly detection:
          - "paraphrase": high semantic similarity + low lexical overlap
            (surface agreement, not genuine -- co-authors used different words
            to say the same thing, which is actually good)
          - "mimicry": low semantic similarity + high lexical overlap
            (copied words but different meaning -- potentially problematic)

        Requires both sentence-transformers AND sklearn.
        Falls back to primary-only if either is unavailable.
        """
        if len(responses) < 2:
            primary = self.detect(responses)
            return BidirectionalWobbleResult(
                primary=primary, secondary=primary,
                ranking_agreement=1.0, self_consistent=True,
                meta_confidence=1.0, anomaly_type=None,
            )

        if not (HAS_SBERT and HAS_SKLEARN):
            primary = self.detect(responses)
            return BidirectionalWobbleResult(
                primary=primary, secondary=primary,
                ranking_agreement=1.0, self_consistent=True,
                meta_confidence=1.0, anomaly_type=None,
            )

        # Run BOTH methods
        sbert_embeddings = self._embed_sentence_transformers(responses)
        tfidf_embeddings = self._embed_tfidf(responses)

        D_semantic = self._cosine_distance_matrix(sbert_embeddings)
        D_lexical = self._cosine_distance_matrix(tfidf_embeddings)

        n = len(responses)

        # Build primary (semantic) WobbleResult
        upper_sem = D_semantic[np.triu_indices(n, k=1)]
        score_sem = float(np.mean(upper_sem)) if len(upper_sem) > 0 else 0.0
        div_sem = [
            (i, j) for i in range(n) for j in range(i + 1, n)
            if D_semantic[i, j] > self._threshold
        ]
        primary = WobbleResult(
            score=score_sem, consensus=score_sem < self._threshold,
            divergent_pairs=div_sem, pairwise_distances=D_semantic,
            n_responses=n, method="sentence_transformers",
        )

        # Build secondary (lexical) WobbleResult
        lex_threshold = self._threshold * self.TFIDF_THRESHOLD_SCALE
        upper_lex = D_lexical[np.triu_indices(n, k=1)]
        score_lex = float(np.mean(upper_lex)) if len(upper_lex) > 0 else 0.0
        div_lex = [
            (i, j) for i in range(n) for j in range(i + 1, n)
            if D_lexical[i, j] > lex_threshold
        ]
        secondary = WobbleResult(
            score=score_lex, consensus=score_lex < lex_threshold,
            divergent_pairs=div_lex, pairwise_distances=D_lexical,
            n_responses=n, method="tfidf",
        )

        # Compute Spearman rank correlation between the two distance vectors
        ranking_agreement = self._spearman_rank_correlation(upper_sem, upper_lex)

        # Self-consistency thresholds
        self_consistent = ranking_agreement > 0.6
        meta_confidence = max(0.0, min(1.0, (ranking_agreement + 1.0) / 2.0))

        # Anomaly detection
        anomaly_type = None
        if ranking_agreement < 0.3:
            # Methods disagree strongly -- check which direction
            if score_sem < self._threshold and score_lex > lex_threshold:
                # Semantically similar but lexically different
                anomaly_type = "paraphrase"
            elif score_sem > self._threshold and score_lex < lex_threshold:
                # Lexically similar but semantically different
                anomaly_type = "mimicry"

        log.debug(
            "Bidirectional wobble: sem=%.4f lex=%.4f rank_agree=%.4f "
            "consistent=%s anomaly=%s",
            score_sem, score_lex, ranking_agreement,
            self_consistent, anomaly_type,
        )

        return BidirectionalWobbleResult(
            primary=primary, secondary=secondary,
            ranking_agreement=ranking_agreement,
            self_consistent=self_consistent,
            meta_confidence=meta_confidence,
            anomaly_type=anomaly_type,
        )

    def isolate_divergence_factor(
        self,
        responses: List[str],
        labels: Optional[List[str]] = None,
    ) -> FactorIsolationResult:
        """v4.3: Leave-one-out analysis to find which co-author drives divergence.

        For N co-authors, run N passes with one excluded each time.
        The co-author whose exclusion produces the largest wobble DROP
        is the divergence factor (the theta_2 in GoZ terminology).

        If that single exclusion accounts for >60% of total wobble,
        you have single-factor divergence -- one outlier co-author.

        Args:
            responses: List of co-author response strings (minimum 3).
            labels: Optional list of co-author names/models for labeling.

        Returns:
            FactorIsolationResult with divergence factor identification.

        Raises:
            ValueError: If fewer than 3 responses provided.
        """
        n = len(responses)
        if n < 3:
            raise ValueError("Factor isolation requires at least 3 responses")

        # Full wobble score with all responses
        full_result = self.detect(responses)
        full_wobble = full_result.score

        # Leave-one-out: compute wobble with each response excluded
        loo_scores = []
        for i in range(n):
            subset = [r for j, r in enumerate(responses) if j != i]
            loo_result = self.detect(subset)
            loo_scores.append(loo_result.score)

        # The response whose exclusion causes the largest wobble DROP
        # is the divergence factor
        drops = [full_wobble - s for s in loo_scores]
        max_drop_idx = int(np.argmax(drops))
        max_drop = drops[max_drop_idx]

        # Drop ratio: what fraction of total wobble is attributable to this one response
        drop_ratio = max_drop / full_wobble if full_wobble > 1e-10 else 0.0

        # Label if available
        factor_label = None
        if labels and max_drop_idx < len(labels):
            factor_label = labels[max_drop_idx]

        log.debug(
            "Factor isolation: full=%.4f drop=%.4f ratio=%.2f factor=%d (%s)",
            full_wobble, max_drop, drop_ratio, max_drop_idx, factor_label,
        )

        return FactorIsolationResult(
            full_wobble=full_wobble,
            leave_one_out_scores=loo_scores,
            divergence_factor_index=max_drop_idx,
            wobble_drop=max_drop,
            drop_ratio=drop_ratio,
            single_factor_divergence=drop_ratio > 0.6,
            factor_label=factor_label,
        )

    @staticmethod
    def _spearman_rank_correlation(x: np.ndarray, y: np.ndarray) -> float:
        """Compute Spearman rank correlation between two 1D arrays.

        Returns a value in [-1, 1]. Handles edge cases (constant arrays,
        length < 2) by returning 1.0 (trivially consistent).
        """
        if len(x) < 2 or len(y) < 2:
            return 1.0

        # Rank both arrays
        def _rank(arr):
            temp = np.argsort(np.argsort(arr)).astype(float)
            return temp

        rx = _rank(x)
        ry = _rank(y)

        # Check for constant arrays
        if np.std(rx) < 1e-15 or np.std(ry) < 1e-15:
            return 1.0

        # Pearson correlation of ranks = Spearman
        n = len(rx)
        d = rx - ry
        rho = 1.0 - (6.0 * np.sum(d ** 2)) / (n * (n ** 2 - 1))
        return float(np.clip(rho, -1.0, 1.0))

    def _embed_sentence_transformers(self, texts: List[str]) -> np.ndarray:
        """Return (N, D) embeddings using sentence-transformers."""
        if self._sbert_model is None:
            log.info("Loading sentence-transformers model: %s", self._model_name)
            self._sbert_model = SentenceTransformer(self._model_name)
        return self._sbert_model.encode(texts, convert_to_numpy=True)

    def _embed_tfidf(self, texts: List[str]) -> np.ndarray:
        """Fallback: Return (N, D) TF-IDF vectors using sklearn."""
        vectorizer = TfidfVectorizer()
        tfidf_matrix = vectorizer.fit_transform(texts)
        return tfidf_matrix.toarray()

    @staticmethod
    def _cosine_distance_matrix(embeddings: np.ndarray) -> np.ndarray:
        """Return (N, N) pairwise cosine distance matrix."""
        # Normalize rows
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normed = embeddings / norms

        # Cosine similarity -> distance
        similarity = normed @ normed.T
        similarity = np.clip(similarity, -1.0, 1.0)
        return 1.0 - similarity
