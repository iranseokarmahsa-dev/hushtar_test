"use strict";

const dataElement = document.querySelector("#paradigm-data");
const canvas = document.querySelector("#paradigm-canvas");
const links = document.querySelector("#paradigm-links");
const zoomContent = document.querySelector("#paradigm-zoom-content");
const viewport = document.querySelector("#paradigm-viewport");
const detailPanel = document.querySelector("#paradigm-detail");
const exportStatus = document.querySelector("#paradigm-export-status");

if (dataElement && canvas && links && zoomContent && viewport && detailPanel) {
	const diagram = JSON.parse(dataElement.textContent || "{}");
	let zoom = 1;
	const svgNamespace = "http://www.w3.org/2000/svg";

	function addText(parent, tag, text, className) {
		const element = document.createElement(tag);
		if (className) element.className = className;
		element.textContent = text || "";
		parent.append(element);
		return element;
	}

	function renderDetails(node) {
		detailPanel.replaceChildren();
		addText(detailPanel, "p", "جزئیات مؤلفه", "eyebrow");
		addText(detailPanel, "h2", node.label);
		if (!node.items.length) {
			addText(detailPanel, "p", "در تحلیل مستقل برای این مؤلفه داده‌ای ثبت نشده است.", "muted");
			return;
		}
		for (const item of node.items) {
			const section = document.createElement("section");
			section.className = "diagram-detail-item";
			addText(section, "h3", item.title);
			if (item.description) addText(section, "p", item.description);

			if (item.categories.length) {
				const categories = document.createElement("div");
				categories.className = "diagram-detail-categories";
				addText(categories, "strong", "مقوله‌های مرتبط");
				for (const category of item.categories) {
					const categoryItem = document.createElement("article");
					addText(categoryItem, "strong", `${category.title} · ${category.id}`, "diagram-detail-category-title");
					if (category.definition) addText(categoryItem, "p", category.definition);
					categories.append(categoryItem);
				}
				section.append(categories);
			}

			appendChips(section, "شناسه مقوله", item.category_ids);
			appendChips(section, "مفاهیم پشتیبان", item.concept_ids);
			appendEvidence(section, item.evidence);
			detailPanel.append(section);
		}

		const categoryIds = new Set(node.category_ids);
		const related = diagram.relationships.filter(
			relationship => categoryIds.has(relationship.source_category_id)
				|| categoryIds.has(relationship.target_category_id),
		);
		if (related.length) {
			const relationSection = document.createElement("section");
			relationSection.className = "diagram-detail-item";
			addText(relationSection, "h3", "روابط ثبت‌شدهٔ مرتبط");
			for (const relation of related) {
				const relationItem = document.createElement("article");
				relationItem.className = "diagram-related-relationship";
				addText(
					relationItem,
					"strong",
					`${relation.relationship_id} · ${relation.source_category_id} ← ${relation.target_category_id}`,
				);
				addText(relationItem, "p", relation.description);
				appendChips(relationItem, "مفاهیم", relation.supporting_concept_ids);
				appendEvidence(relationItem, relation.evidence);
				relationSection.append(relationItem);
			}
			detailPanel.append(relationSection);
		}
	}

	function appendChips(parent, label, values) {
		if (!values?.length) return;
		const group = document.createElement("div");
		group.className = "diagram-detail-chips";
		addText(group, "strong", label);
		const chips = document.createElement("div");
		chips.className = "diagram-chip-list";
		for (const value of values) addText(chips, "span", value);
		group.append(chips);
		parent.append(group);
	}

	function appendEvidence(parent, evidence) {
		if (!evidence?.length) {
			addText(parent, "p", "شاهد پشتیبان مستقیمی برای این مؤلفه ثبت نشده است.", "muted");
			return;
		}
		const disclosure = document.createElement("details");
		disclosure.className = "diagram-evidence";
		addText(disclosure, "summary", `شواهد پشتیبان (${evidence.length})`);
		for (const item of evidence) {
			const quote = document.createElement("blockquote");
			quote.append(document.createTextNode(`${item.paragraph_id || ""} «${item.exact_quote || ""}»`));
			if (item.interpretation) addText(quote, "small", item.interpretation);
			disclosure.append(quote);
		}
		parent.append(disclosure);
	}

	function drawEdges() {
		const width = canvas.clientWidth;
		const height = canvas.clientHeight;
		links.setAttribute("viewBox", `0 0 ${width} ${height}`);
		links.setAttribute("width", width);
		links.setAttribute("height", height);
		links.querySelectorAll(".paradigm-edge").forEach(edge => edge.remove());
		const canvasRect = canvas.getBoundingClientRect();

		for (const edge of diagram.edges) {
			const source = document.querySelector(`#paradigm-node-${CSS.escape(edge.source)}`);
			const target = document.querySelector(`#paradigm-node-${CSS.escape(edge.target)}`);
			if (!source || !target) continue;
			const sourceRect = source.getBoundingClientRect();
			const targetRect = target.getBoundingClientRect();
			const start = {
				x: (sourceRect.left + sourceRect.width / 2 - canvasRect.left) / zoom,
				y: (sourceRect.top + sourceRect.height / 2 - canvasRect.top) / zoom,
			};
			const end = {
				x: (targetRect.left + targetRect.width / 2 - canvasRect.left) / zoom,
				y: (targetRect.top + targetRect.height / 2 - canvasRect.top) / zoom,
			};
			const dx = end.x - start.x;
			const dy = end.y - start.y;
			const direction = Math.sign(dx);
			const sourceX = start.x + direction * sourceRect.width / (2 * zoom);
			const targetX = end.x - direction * targetRect.width / (2 * zoom);
			const pathData = Math.abs(dy) < 2
				? `M ${sourceX} ${start.y} L ${targetX} ${end.y}`
				: `M ${sourceX} ${start.y} L ${(sourceX + targetX) / 2} ${start.y} L ${(sourceX + targetX) / 2} ${end.y} L ${targetX} ${end.y}`;
			const path = document.createElementNS(svgNamespace, "path");
			path.classList.add("paradigm-edge", `is-${edge.kind}`);
			path.setAttribute("d", pathData);
			path.setAttribute("fill", "none");
			path.setAttribute("stroke", "#111111");
			path.setAttribute("stroke-width", "1.5");
			path.setAttribute("marker-end", "url(#paradigm-arrow)");
			const title = document.createElementNS(svgNamespace, "title");
			title.textContent = edge.label;
			path.append(title);
			links.append(path);
		}
	}

	function setZoom(nextZoom) {
		zoom = Math.min(1.5, Math.max(0.3, nextZoom));
		const width = canvas.offsetWidth * zoom;
		const height = canvas.offsetHeight * zoom;
		zoomContent.style.width = `${width}px`;
		zoomContent.style.height = `${height}px`;
		canvas.style.transform = `scale(${zoom})`;
		requestAnimationFrame(drawEdges);
	}

	function fitDiagram() {
		const availableWidth = Math.max(280, viewport.clientWidth - 24);
		const scale = Math.min(1, availableWidth / canvas.offsetWidth);
		setZoom(scale);
		viewport.scrollTo({ top: 0, left: 0, behavior: "smooth" });
	}

	for (const node of diagram.nodes) {
		const button = document.querySelector(`#paradigm-node-${CSS.escape(node.id)}`);
		button?.addEventListener("click", () => {
			document.querySelectorAll(".paradigm-node").forEach(item => {
				item.setAttribute("aria-pressed", String(item === button));
			});
			renderDetails(node);
		});
	}

	document.querySelectorAll("[data-zoom]").forEach(button => {
		button.addEventListener("click", () => {
			const action = button.dataset.zoom;
			if (action === "in") setZoom(zoom + 0.15);
			else if (action === "out") setZoom(zoom - 0.15);
			else fitDiagram();
		});
	});

	document.querySelector("[data-print]")?.addEventListener("click", () => window.print());

	document.querySelector("[data-export='png']")?.addEventListener("click", async () => {
		exportStatus.textContent = "در حال آماده‌سازی تصویر PNG…";
		try {
			const png = await createPng();
			const url = URL.createObjectURL(png);
			const download = document.createElement("a");
			download.href = url;
			download.download = "paradigm-diagram.png";
			document.body.append(download);
			download.click();
			download.remove();
			window.setTimeout(() => URL.revokeObjectURL(url), 1000);
			exportStatus.textContent = "تصویر PNG دریافت شد.";
		} catch (error) {
			console.error("PNG export failed", error);
			exportStatus.textContent = "ساخت PNG ناموفق بود. از گزینهٔ چاپ / PDF استفاده کنید.";
		}
	});

	async function createPng() {
		const width = canvas.offsetWidth;
		const height = canvas.offsetHeight;
		const clone = canvas.cloneNode(true);
		clone.querySelector(".paradigm-links")?.remove();
		clone.querySelectorAll(".paradigm-node").forEach(node => {
			node.style.position = "absolute";
			const original = document.querySelector(`#${CSS.escape(node.id)}`);
			const rect = original.getBoundingClientRect();
			const canvasRect = canvas.getBoundingClientRect();
			node.style.left = `${(rect.left - canvasRect.left) / zoom}px`;
			node.style.top = `${(rect.top - canvasRect.top) / zoom}px`;
			node.style.width = `${rect.width / zoom}px`;
			node.style.height = `${rect.height / zoom}px`;
			node.style.margin = "0";
		});
		clone.style.position = "relative";
		clone.style.width = `${width}px`;
		clone.style.height = `${height}px`;
		clone.style.transform = "none";
		const foreignStyle = `
			*{box-sizing:border-box;font-family:Vazirmatn,Tahoma,sans-serif}
			.paradigm-canvas{background:#fff}
			.paradigm-node{display:block;padding:12px;border:1px solid #111;border-radius:0;background:#fff;color:#24312d;text-align:right;direction:rtl}
			.paradigm-node-heading{display:flex;gap:9px;align-items:center;margin-bottom:12px;color:#111;font-size:16px}
			.paradigm-node-index,.paradigm-node-hint{display:none}
			.paradigm-node-item{display:block;padding:9px 0;border-top:1px solid #e8eee9;font-size:12px;line-height:1.6}
			.paradigm-node-item strong,.paradigm-node-item small{display:block}
			.paradigm-node-item small{color:#697671}
			.paradigm-node-categories{border:0;background:transparent}
			.paradigm-node-categories .paradigm-node-heading,.paradigm-node-categories .paradigm-node-hint,.paradigm-node-categories .paradigm-node-item small{display:none}
			.paradigm-node-categories .paradigm-node-items{display:grid;gap:10px;justify-items:center}
			.paradigm-node-categories .paradigm-node-item{display:grid;align-content:center;justify-items:center;width:104px;min-height:68px;padding:8px;border:1px solid #111;border-radius:50%;text-align:center}
		`;
		const foreign = `<foreignObject x="0" y="0" width="${width}" height="${height}"><div xmlns="http://www.w3.org/1999/xhtml"><style>${foreignStyle}</style>${new XMLSerializer().serializeToString(clone)}</div></foreignObject>`;
		const edgeMarkup = Array.from(links.querySelectorAll(".paradigm-edge"))
			.map(path => new XMLSerializer().serializeToString(path))
			.join("");
		const marker = '<defs><marker id="paradigm-arrow" markerWidth="10" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0,0 L9,4 L0,8 Z" fill="#111111"/></marker></defs>';
		const svg = `<svg xmlns="${svgNamespace}" width="${width * 2}" height="${height * 2}" viewBox="0 0 ${width} ${height}">${marker}<rect width="100%" height="100%" fill="#fbfdfb"/>${edgeMarkup}${foreign}</svg>`;
		const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
		const image = new Image();
		image.src = URL.createObjectURL(blob);
		try {
			await image.decode();
			const output = document.createElement("canvas");
			output.width = width * 2;
			output.height = height * 2;
			const context = output.getContext("2d");
			if (!context) throw new Error("Canvas 2D context is unavailable");
			context.drawImage(image, 0, 0);
			const pngBlob = await new Promise(resolve => output.toBlob(resolve, "image/png"));
			if (!pngBlob) throw new Error("PNG encoding failed");
			return pngBlob;
		} finally {
			URL.revokeObjectURL(image.src);
		}
	}

	const observer = new ResizeObserver(drawEdges);
	observer.observe(canvas);
	window.addEventListener("resize", drawEdges);
	requestAnimationFrame(fitDiagram);
}
