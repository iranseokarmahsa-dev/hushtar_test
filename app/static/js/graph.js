"use strict";

const graphElement = document.querySelector("#relationship-graph");
const graphData = graphElement ? JSON.parse(graphElement.dataset.graph || "{}") : null;

if (graphElement && graphData?.nodes?.length && window.vis) {
	const options = {
		autoResize: true,
		nodes: {
			shape: "box",
			margin: 14,
			borderWidth: 1,
			color: { background: "#e4eee8", border: "#678779", highlight: { background: "#f1e4c4", border: "#b95d3d" } },
			font: { face: "Vazirmatn, Tahoma, sans-serif", color: "#24312d", size: 15 },
		},
		edges: {
			arrows: { to: { enabled: true, scaleFactor: 0.7 } },
			color: { color: "#71817a", highlight: "#b95d3d" },
			font: { face: "Tahoma, sans-serif", size: 10, align: "middle" },
			smooth: { type: "dynamic" },
		},
		interaction: { hover: true, navigationButtons: true, keyboard: true },
		physics: { stabilization: { iterations: 120 }, barnesHut: { gravitationalConstant: -2600, springLength: 180 } },
	};
	new window.vis.Network(graphElement, graphData, options);
} else if (graphElement) {
	graphElement.textContent = "برای نمایش نمودار، دسته‌ها و روابط باید در تحلیل ثبت شده باشند و کتابخانه نمودار در دسترس باشد.";
}
