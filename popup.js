function getHeader(headers, headerName) {
  const item = headers.find((header) => header.name.toLowerCase() === headerName.toLowerCase());
  return item ? item.value : "";
}

function downloadJson(data, fileName) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const SERVER_URL = "http://localhost:5000";
const NATIVE_HOST_NAME = "com.email_analysis.server";

function startServer() {
  return new Promise((resolve, reject) => {
    const port = chrome.runtime.connectNative(NATIVE_HOST_NAME);

    port.onMessage.addListener((message) => {
      port.disconnect();
      if (message.ok) {
        resolve(message);
      } else {
        reject(new Error(message.error || "Unable to start the server."));
      }
    });

    port.onDisconnect.addListener(() => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      }
    });

    port.postMessage({ action: "start-server" });
  });
}

async function openServerLogin() {
  await chrome.tabs.create({ url: `${SERVER_URL}/login` });
}

async function fetchServerEmails() {
  let response;
  try {
    response = await fetch(`${SERVER_URL}/emails`);
  } catch (error) {
    throw new Error(
      "Server unavailable. Check that the native host is installed and that server.py is running."
    );
  }
  const contentType = response.headers.get("content-type") || "";
  let data;

  if (contentType.includes("application/json")) {
    data = await response.json();
  } else {
    const responseText = await response.text();
    throw new Error(
      `Unexpected server response (${response.status}). ${
        responseText.slice(0, 160) || "No details available."
      }`
    );
  }

  if (!response.ok) {
    const error = new Error(data.details || data.error || "Gmail Authentification needed");
    error.code = response.status;
    throw error;
  }

  return data;
}

async function fetchInboxMessages(token) {
  const listUrl = "https://gmail.googleapis.com/gmail/v1/users/me/messages?labelIds=INBOX&maxResults=100";

  const listResponse = await fetch(listUrl, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
  });

  if (!listResponse.ok) {
    const errorText = await listResponse.text();
    throw new Error(`Gmail API Error: ${listResponse.status} - ${errorText}`);
  }

  const listData = await listResponse.json();
  const messages = listData.messages || [];

  const result = {
    total: 0,
    read_count: 0,
    unread_count: 0,
    read: [],
    unread: [],
    all: []
  };

  for (const message of messages) {
    const detailUrl = `https://gmail.googleapis.com/gmail/v1/users/me/messages/${message.id}?format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Subject&metadataHeaders=Date`;

    const detailResponse = await fetch(detailUrl, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
      },
    });

    if (!detailResponse.ok) {
      continue;
    }

    const detail = await detailResponse.json();
    const headers = detail.payload?.headers || [];
    const isRead = !(detail.labelIds || []).includes("UNREAD");

    const email = {
      id: detail.id,
      subject: getHeader(headers, "Subject"),
      from: getHeader(headers, "From"),
      to: getHeader(headers, "To"),
      date: getHeader(headers, "Date"),
      read: isRead,
      status: isRead ? "read" : "unread",
      labels: detail.labelIds || [],
    };

    result.all.push(email);

    if (isRead) {
      result.read.push(email);
      result.read_count += 1;
    } else {
      result.unread.push(email);
      result.unread_count += 1;
    }
  }

  result.total = result.all.length;
  return result;
}

async function analyzeEmailWithAI(emailText) {
  const response = await fetch(`${SERVER_URL}/ai/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: emailText })
  });
  return await response.json();
}

document.getElementById("btn-analyze").addEventListener("click", async () => {
  const resultDiv = document.getElementById("result");
  resultDiv.textContent = "Starting local server…";

  try {
    await startServer();
    resultDiv.textContent = "Connecting to Gmail…";
    const data = await fetchServerEmails();
    downloadJson(data, "emails.json");
    resultDiv.textContent = `Export OK : ${data.total} mails (${data.read_count} read / ${data.unread_count} unread)`;
  } catch (error) {
    if (error.code === 401) {
      await openServerLogin();
      resultDiv.textContent = "Connect to Gmail in the opened tab, then click Export again.";
      return;
    }
    resultDiv.textContent = "Error during export : " + error.message;
  }
});

document.getElementById("btn-switch-account").addEventListener("click", async () => {
  const resultDiv = document.getElementById("result");
  resultDiv.textContent = "Selecting Google account…";

  try {
    await fetch(`${SERVER_URL}/logout`);
    await openServerLogin();
    resultDiv.textContent = "Connect the new account in the opened tab.";
  } catch (error) {
    resultDiv.textContent = "Unable to open Google : " + error.message;
  }
});

document.getElementById("btn-logout").addEventListener("click", async () => {
  const resultDiv = document.getElementById("result");
  resultDiv.textContent = "Sign out...";

  try {
    await fetch(`${SERVER_URL}/logout`);

    resultDiv.textContent = "Gmail signed out.";
  } catch (error) {
    console.error(error);
    resultDiv.textContent = "Error signing out of Gmail: " + error.message;
  }
});
