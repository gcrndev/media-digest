export const appHtml = `<!doctype html>
<html lang="pt">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Media Digest</title>
    <style>
      body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f7f7f4; color: #171717; }
      main { max-width: 720px; margin: 0 auto; padding: 32px 20px; }
      h1 { font-size: 28px; margin: 0 0 8px; }
      p { line-height: 1.5; color: #444; }
      form { display: grid; gap: 12px; margin-top: 24px; }
      input, button { font: inherit; border-radius: 8px; border: 1px solid #c8c8c0; padding: 12px; }
      button { background: #101010; color: white; cursor: pointer; }
      pre { white-space: pre-wrap; background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Media Digest</h1>
      <p>Capture links from YouTube and Instagram Reels, then process them locally into your vault.</p>
      <form id="capture-form">
        <input id="token" name="token" placeholder="Capture token" autocomplete="off">
        <input id="url" name="url" placeholder="https://youtube.com/..." inputmode="url">
        <button type="submit">Capture link</button>
      </form>
      <pre id="result"></pre>
    </main>
    <script>
      const form = document.querySelector("#capture-form");
      const result = document.querySelector("#result");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        result.textContent = "Saving...";
        const response = await fetch("/api/media/capture-token", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "authorization": "Bearer " + document.querySelector("#token").value
          },
          body: JSON.stringify({
            url: document.querySelector("#url").value,
            source: "web-form",
            captured_at: new Date().toISOString()
          })
        });
        result.textContent = JSON.stringify(await response.json(), null, 2);
      });
    </script>
  </body>
</html>`;

export const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#111"/><path d="M18 20h28v24H18z" fill="#fff"/><path d="M28 26l12 6-12 6z" fill="#111"/></svg>`;
