"use strict";

const uploadForm = document.querySelector("[data-upload-start]");
if (uploadForm) {
	const fileInput = uploadForm.querySelector("[data-upload-file]");
	const titleInput = uploadForm.querySelector("[data-project-title]");
	const filename = uploadForm.querySelector("[data-upload-filename]");
	const status = uploadForm.querySelector("[data-upload-status]");
	const submitButton = uploadForm.querySelector("[data-upload-submit]");
	const dropzone = uploadForm.querySelector("[data-upload-drop]");
	const initialButtonText = submitButton.textContent;

	function updateSelectedFile(file) {
		if (!file) return;
		filename.textContent = file.name;
		filename.hidden = false;
		titleInput.value = file.name.replace(/\.docx$/i, "").trim() || "مصاحبه جدید";
		submitButton.disabled = false;
		status.hidden = true;
		dropzone.classList.remove("is-invalid");
	}

	function showUploadError(message) {
		status.textContent = message;
		status.hidden = false;
		dropzone.classList.add("is-invalid");
	}

	function responseError(response) {
		return response.json()
			.then((body) => body.detail || "بارگذاری فایل انجام نشد. دوباره تلاش کنید.")
			.catch(() => "بارگذاری فایل انجام نشد. دوباره تلاش کنید.");
	}

	fileInput.addEventListener("change", () => updateSelectedFile(fileInput.files[0]));
	["dragenter", "dragover"].forEach((eventName) => {
		dropzone.addEventListener(eventName, (event) => {
			event.preventDefault();
			dropzone.classList.add("is-dragging");
		});
	});
	["dragleave", "drop"].forEach((eventName) => {
		dropzone.addEventListener(eventName, (event) => {
			event.preventDefault();
			dropzone.classList.remove("is-dragging");
		});
	});
	dropzone.addEventListener("drop", (event) => {
		const [file] = event.dataTransfer.files;
		if (!file) return;
		const transfer = new DataTransfer();
		transfer.items.add(file);
		fileInput.files = transfer.files;
		updateSelectedFile(file);
	});

	uploadForm.addEventListener("submit", async (event) => {
		event.preventDefault();
		const [file] = fileInput.files;
		if (!file) return;
		if (!file.name.toLowerCase().endsWith(".docx")) {
			showUploadError("لطفاً یک فایل با فرمت DOCX انتخاب کنید.");
			return;
		}
		if (file.size > 20 * 1024 * 1024) {
			showUploadError("حجم فایل باید کمتر از ۲۰ مگابایت باشد.");
			return;
		}

		submitButton.disabled = true;
		submitButton.textContent = "در حال بارگذاری...";
		status.textContent = "فایل در حال آماده‌سازی است.";
		status.hidden = false;
		try {
			const createResponse = await fetch(uploadForm.action, {
				method: "POST",
				headers: { "Content-Type": "application/x-www-form-urlencoded" },
				body: new URLSearchParams({ title: titleInput.value, topic: "" }),
			});
			if (!createResponse.ok) throw new Error(await responseError(createResponse));

			const projectPath = new URL(createResponse.url).pathname;
			if (!/^\/projects\/[^/]+$/.test(projectPath)) throw new Error("ساخت پروژه انجام نشد. دوباره تلاش کنید.");
			const fileData = new FormData();
			fileData.append("interview", file);
			status.textContent = "مصاحبه در حال پردازش است.";
			const uploadResponse = await fetch(`${projectPath}/interview`, { method: "POST", body: fileData });
			if (!uploadResponse.ok) throw new Error(await responseError(uploadResponse));
			window.location.assign(uploadResponse.url);
		} catch (error) {
			showUploadError(error.message || "ارتباط با سرور برقرار نشد. دوباره تلاش کنید.");
			submitButton.disabled = false;
			submitButton.textContent = initialButtonText;
		}
	});
}

function updateWitnessPreview(select) {
	const row = select.closest(".concept-row");
	const preview = row?.querySelector(".witness-preview");
	if (!preview) return;
	const paragraph = [...document.querySelectorAll(".paragraph-list [data-paragraph-id]")]
		.find((item) => item.dataset.paragraphId === select.value);
	const id = preview.querySelector("[data-witness-id]");
	const text = preview.querySelector("[data-witness-text]");
	if (id) id.textContent = select.value;
	if (text) text.textContent = paragraph?.querySelector("p")?.textContent ?? "";
}

document.querySelectorAll(".concept-row select[name='paragraph_ids']").forEach((select) => {
	select.addEventListener("change", () => updateWitnessPreview(select));
});

document.querySelectorAll("[data-add-concept]").forEach((button) => {
	button.addEventListener("click", () => {
		const template = document.querySelector("#concept-row-template");
		const rows = document.querySelector("#concept-rows");
		if (!template || !rows) return;
		document.querySelector("#no-concepts-row")?.remove();
		const row = template.content.cloneNode(true);
		rows.append(row);
		const select = rows.lastElementChild?.querySelector("select[name='paragraph_ids']");
		if (select) {
			select.addEventListener("change", () => updateWitnessPreview(select));
			updateWitnessPreview(select);
		}
	});
});

document.querySelectorAll("[data-copy]").forEach((button) => {
	button.addEventListener("click", async () => {
		const source = document.querySelector(button.dataset.copy);
		if (!source) return;
		try {
			await navigator.clipboard.writeText(source.value);
			const originalText = button.textContent;
			button.textContent = "کپی شد";
			window.setTimeout(() => { button.textContent = originalText; }, 1400);
		} catch {
			source.focus();
			source.select();
		}
	});
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
	form.addEventListener("submit", (event) => {
		if (!window.confirm(form.dataset.confirm)) event.preventDefault();
	});
});
