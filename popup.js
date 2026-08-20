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

async function openServerLogin() {
  await chrome.tabs.create({ url: `${SERVER_URL}/login` });
}

async function fetchServerEmails() {
  const response = await fetch(`${SERVER_URL}/emails`);
  const data = await response.json();

  if (!response.ok) {
    const error = new Error(data.details || data.error || "Authentification Gmail requise");
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
    throw new Error(`Erreur Gmail API: ${listResponse.status} - ${errorText}`);
  }

  const listData = await listResponse.json();
  const messages = listData.messages || [];

  const result = {
    total: 0,
    read: 0,
    unread: 0,
    read: [],
    unread: [],
    all: [],
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
      result.read += 1;
    } else {
      result.unread.push(email);
      result.unread += 1;
    }
  }

  result.total = result.all.length;
  return result;
}

document.getElementById("btn-analyze").addEventListener("click", async () => {
  const resultDiv = document.getElementById("result");
  resultDiv.textContent = "Connecting to Gmail…";

  try {
    const data = await fetchServerEmails();
    downloadJson(data, "emails.json");
    resultDiv.textContent = `Export OK : ${data.total} mails (${data.read} read / ${data.unread} unread)`;
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

