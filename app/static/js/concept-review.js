"use strict";

const reviewForm = document.querySelector("[data-concept-review]");
if (reviewForm) {
	const cards = [...reviewForm.querySelectorAll("[data-concept-card]")];
	const searchInput = reviewForm.querySelector("[data-concept-search]");
	const statusFilter = reviewForm.querySelector("[data-concept-filter]");
	const codeFilter = reviewForm.querySelector("[data-code-filter]");
	const pagination = reviewForm.querySelector("[data-concept-pagination]");
	const pageStatus = reviewForm.querySelector("[data-page-status]");
	const resultCount = reviewForm.querySelector("[data-result-count]");
	const filterEmpty = reviewForm.querySelector("[data-filter-empty]");
	const saveDraftButton = reviewForm.querySelector("[data-save-active]");
	const pageSize = 20;
	const storageKey = `concept-review:${reviewForm.dataset.projectId}`;
	let savedState = {};
	try {
		savedState = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
	} catch {
		sessionStorage.removeItem(storageKey);
	}
	if (!savedState || typeof savedState !== "object") savedState = {};
	if (savedState.status) statusFilter.value = savedState.status;
	if (savedState.code) codeFilter.value = savedState.code;
	if (typeof savedState.search === "string") searchInput.value = savedState.search;
	let currentPage = Number.isInteger(savedState.page) && savedState.page > 0 ? savedState.page : 1;
	let activeCardId = "";
	let filteredCards = [];

	function persistState() {
		sessionStorage.setItem(storageKey, JSON.stringify({
			status: statusFilter.value,
			code: codeFilter.value,
			search: searchInput.value,
			page: currentPage,
			activeCardId,
		}));
	}

	function matchesFilters(card) {
		const statusMatches = statusFilter.value === "all"
			|| (statusFilter.value === "pending" && ["pending", "suggestion"].includes(card.dataset.status))
			|| statusFilter.value === card.dataset.status;
		const codeMatches = codeFilter.value === "all" || codeFilter.value === card.dataset.code;
		return statusMatches
			&& codeMatches
			&& card.dataset.search.toLocaleLowerCase("fa").includes(searchInput.value.trim().toLocaleLowerCase("fa"));
	}

	function setDetailControlsEnabled(card, enabled) {
		card.querySelectorAll("[data-concept-detail] input, [data-concept-detail] textarea, [data-concept-detail] button")
			.forEach((control) => { control.disabled = !enabled; });
	}

	function discardUnsavedDetails(card) {
		if (!card || card.dataset.dirty !== "true") return true;
		if (!window.confirm("تغییرات ذخیره‌نشده این مفهوم کنار گذاشته شوند؟")) return false;
		card.dataset.dirty = "false";
		const detail = card.querySelector("[data-concept-detail]");
		detail.replaceChildren();
		detail.dataset.loaded = "false";
		return true;
	}

	function closeDetails(exceptCard) {
		cards.forEach((card) => {
			if (card === exceptCard) return;
			const detail = card.querySelector("[data-concept-detail]");
			detail.hidden = true;
			card.classList.remove("is-open");
			setDetailControlsEnabled(card, false);
		});
	}

	function highlightEvidence(detail) {
		detail.querySelectorAll("[data-highlight-quotes]").forEach((paragraph) => {
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
			ranges.sort((a, b) => a.start - b.start || b.end - a.end);
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

	function bindDetailControls(card) {
		const detail = card.querySelector("[data-concept-detail]");
		detail.querySelectorAll("input:not([type=hidden]), textarea").forEach((field) => {
			field.addEventListener("input", () => { card.dataset.dirty = "true"; });
		});
		detail.querySelector("[data-concept-previous]")?.addEventListener("click", () => {
			navigateTo(card, -1);
		});
		detail.querySelector("[data-concept-next]")?.addEventListener("click", () => {
			navigateTo(card, 1);
		});
		detail.querySelector("[data-next-pending]")?.addEventListener("click", () => {
			navigateToNextPending(card);
		});
	}

	async function openCard(card, { scroll = false } = {}) {
		if (!card) return;
		const activeCard = cards.find((item) => item.dataset.conceptId === activeCardId);
		if (activeCard && activeCard !== card && !discardUnsavedDetails(activeCard)) return;
		closeDetails(card);
		const detail = card.querySelector("[data-concept-detail]");
		detail.hidden = false;
		card.classList.add("is-open");
		activeCardId = card.dataset.conceptId;
		persistState();

		if (detail.dataset.loaded !== "true") {
			detail.textContent = "جزئیات در حال بارگذاری است…";
			try {
				const response = await fetch(card.dataset.detailUrl, {
					headers: { Accept: "text/html" },
				});
				if (!response.ok) throw new Error("بارگذاری جزئیات مفهوم انجام نشد.");
				detail.innerHTML = await response.text();
				detail.dataset.loaded = "true";
				card.dataset.dirty = "false";
				bindDetailControls(card);
				highlightEvidence(detail);
			} catch (error) {
				detail.replaceChildren();
				const message = document.createElement("p");
				message.className = "concept-detail-error";
				message.setAttribute("role", "alert");
				message.textContent = error.message || "بارگذاری جزئیات مفهوم انجام نشد.";
				detail.append(message);
				saveDraftButton.hidden = true;
			}
		}
		setDetailControlsEnabled(card, true);
		saveDraftButton.hidden = detail.dataset.loaded !== "true";
		if (scroll) card.scrollIntoView({ behavior: "smooth", block: "start" });
	}

	function render() {
		filteredCards = cards.filter(matchesFilters);
		const pageCount = Math.max(1, Math.ceil(filteredCards.length / pageSize));
		currentPage = Math.min(currentPage, pageCount);
		const start = (currentPage - 1) * pageSize;
		const pageCards = new Set(filteredCards.slice(start, start + pageSize));
		cards.forEach((card) => {
			card.hidden = !pageCards.has(card);
			if (!pageCards.has(card)) {
				card.querySelector("[data-concept-detail]").hidden = true;
				card.classList.remove("is-open");
			}
		});
		resultCount.textContent = `${filteredCards.length} مفهوم`;
		filterEmpty.hidden = filteredCards.length > 0;
		pagination.hidden = filteredCards.length <= pageSize;
		pageStatus.textContent = `صفحه ${currentPage} از ${pageCount}`;
		reviewForm.querySelector("[data-page-previous]").disabled = currentPage <= 1;
		reviewForm.querySelector("[data-page-next]").disabled = currentPage >= pageCount;
		persistState();
	}

	function navigateTo(card, direction) {
		const index = filteredCards.indexOf(card);
		const targetIndex = index + direction;
		if (index < 0 || targetIndex < 0 || targetIndex >= filteredCards.length) return;
		const target = filteredCards[targetIndex];
		currentPage = Math.floor(targetIndex / pageSize) + 1;
		render();
		openCard(target, { scroll: true });
	}

	function navigateToNextPending(card) {
		const start = cards.indexOf(card);
		for (let offset = 1; offset < cards.length; offset += 1) {
			const next = cards[(start + offset) % cards.length];
			if (!["pending", "suggestion"].includes(next.dataset.status)) continue;
			if (!filteredCards.includes(next)) {
				statusFilter.value = "pending";
				searchInput.value = "";
				codeFilter.value = "all";
				currentPage = 1;
				render();
			}
			const index = filteredCards.indexOf(next);
			currentPage = Math.floor(index / pageSize) + 1;
			render();
			openCard(next, { scroll: true });
			return;
		}
	}

	searchInput.addEventListener("input", () => {
		currentPage = 1;
		render();
	});
	[statusFilter, codeFilter].forEach((filter) => filter.addEventListener("change", () => {
		currentPage = 1;
		render();
	}));
	reviewForm.querySelector("[data-page-previous]").addEventListener("click", () => {
		currentPage -= 1;
		render();
	});
	reviewForm.querySelector("[data-page-next]").addEventListener("click", () => {
		currentPage += 1;
		render();
	});
	cards.forEach((card) => {
		card.querySelector("[data-open-concept]").addEventListener("click", () => {
			const index = filteredCards.indexOf(card);
			if (index >= 0) currentPage = Math.floor(index / pageSize) + 1;
			render();
			openCard(card);
		});
	});

	render();
	const queryOpenId = reviewForm.dataset.openConcept;
	let restoredCard = cards.find((card) => card.dataset.conceptId === queryOpenId);
	if (restoredCard && !filteredCards.includes(restoredCard)) {
		statusFilter.value = "pending";
		codeFilter.value = "all";
		searchInput.value = "";
		currentPage = 1;
		render();
	}
	restoredCard ||= cards.find((card) => card.dataset.conceptId === savedState.activeCardId);
	if (restoredCard) {
		const index = filteredCards.indexOf(restoredCard);
		if (index >= 0) {
			currentPage = Math.floor(index / pageSize) + 1;
			render();
			openCard(restoredCard);
		}
	}
}
