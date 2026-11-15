"use strict";

const analysisAccordions = [...document.querySelectorAll("[data-analysis-accordion]")];
const categoryCards = [...document.querySelectorAll("[data-category-card]")];
const paradigmCards = [...document.querySelectorAll(".paradigm-component-card")];

function closeSiblings(details, collection) {
	if (!details.open) return;
	collection.forEach((item) => {
		if (item !== details) item.open = false;
	});
}

analysisAccordions.forEach((details) => {
	details.addEventListener("toggle", () => closeSiblings(details, analysisAccordions));
});

categoryCards.forEach((details) => {
	details.addEventListener("toggle", () => closeSiblings(details, categoryCards));
});

paradigmCards.forEach((details) => {
	details.addEventListener("toggle", () => closeSiblings(details, paradigmCards));
});

function openTarget(target) {
	let current = target;
	while (current) {
		if (current instanceof HTMLDetailsElement) {
			current.open = true;
			if (current.matches("[data-analysis-accordion]")) {
				closeSiblings(current, analysisAccordions);
			}
		}
		current = current.parentElement;
	}
}

document.addEventListener("click", (event) => {
	const link = event.target.closest('a[href^="#"]');
	if (!link) return;
	const targetId = link.dataset.openTarget || decodeURIComponent(link.hash.slice(1));
	const target = document.getElementById(targetId);
	if (!target) return;
	openTarget(target);
	window.requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
});

function highlightEvidence() {
	document.querySelectorAll("[data-highlight-quotes]").forEach((paragraph) => {
		const quotes = JSON.parse(paragraph.dataset.highlightQuotes || "[]").filter(Boolean);
		const text = paragraph.textContent;
		const ranges = [];
		quotes.forEach((quote) => {
			let index = text.indexOf(quote);
			while (index !== -1) {
				ranges.push({ start: index, end: index + quote.length });
				index = text.indexOf(quote, index + quote.length);
			}
		});
		ranges.sort((left, right) => left.start - right.start || right.end - left.end);
		const nonOverlapping = ranges.filter((range, index) => (
			index === 0 || range.start >= ranges[index - 1].end
		));
		if (!nonOverlapping.length) return;

		const fragment = document.createDocumentFragment();
		let cursor = 0;
		nonOverlapping.forEach((range) => {
			fragment.append(document.createTextNode(text.slice(cursor, range.start)));
			const mark = document.createElement("mark");
			mark.textContent = text.slice(range.start, range.end);
			fragment.append(mark);
			cursor = range.end;
		});
		fragment.append(document.createTextNode(text.slice(cursor)));
		paragraph.replaceChildren(fragment);
	});
}

highlightEvidence();

const kindFilter = document.querySelector("[data-review-filter-kind]");
const severityFilter = document.querySelector("[data-review-filter-severity]");
const reviewItems = [...document.querySelectorAll("[data-review-list] .analysis-review-item")];
const filterCount = document.querySelector("[data-review-filter-count]");

function filterReviewItems() {
	let visibleCount = 0;
	reviewItems.forEach((item) => {
		const kindMatches = kindFilter.value === "all" || item.dataset.kind === kindFilter.value;
		const severityMatches = severityFilter.value === "all"
			|| item.dataset.severity === severityFilter.value;
		const visible = kindMatches && severityMatches;
		item.hidden = !visible;
		if (visible) visibleCount += 1;
	});
	if (filterCount) {
		filterCount.textContent = `${visibleCount} از ${reviewItems.length} مورد`;
	}
}

kindFilter?.addEventListener("change", filterReviewItems);
severityFilter?.addEventListener("change", filterReviewItems);

const initialTarget = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
if (initialTarget) openTarget(initialTarget);
