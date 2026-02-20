import json
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from grizzyclaw import __version__
from grizzyclaw.config import Settings
from grizzyclaw.agent.core import AgentCore
from grizzyclaw.security import SecurityManager, RateLimiter

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>GrizzyClaw</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            background: #16213e;
            padding: 1rem;
            border-bottom: 2px solid #e94560;
        }
        .header h1 { color: #e94560; }
        .chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 1rem;
        }
        .message {
            margin-bottom: 1rem;
            padding: 0.8rem;
            border-radius: 8px;
            max-width: 80%;
        }
        .message.user {
            background: #0f3460;
            margin-left: auto;
        }
        .message.assistant {
            background: #16213e;
            margin-right: auto;
        }
        .input-container {
            padding: 1rem;
            background: #16213e;
            display: flex;
            gap: 0.5rem;
        }
        input[type="text"] {
            flex: 1;
            padding: 0.8rem;
            border: none;
            border-radius: 4px;
            background: #0f3460;
            color: #fff;
        }
        button {
            padding: 0.8rem 1.5rem;
            background: #e94560;
            border: none;
            border-radius: 4px;
            color: #fff;
            cursor: pointer;
        }
        button:hover { background: #ff6b6b; }
        .status { padding: 0.5rem; text-align: center; font-size: 0.8rem; }
        .status.connected { color: #4ecca3; }
        .status.disconnected { color: #e94560; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🐻 GrizzyClaw</h1>
    </div>
    <div class="status disconnected" id="status">Disconnected</div>
    <div class="chat-container" id="chat"></div>
    <div class="input-container">
        <input type="text" id="message" placeholder="Type your message..." autocomplete="off">
        <button onclick="sendMessage()">Send</button>
    </div>
    <script>
        let ws;
        const chat = document.getElementById('chat');
        const status = document.getElementById('status');
        const messageInput = document.getElementById('message');
        
        function connect() {
            ws = new WebSocket('ws://' + window.location.host + '/ws');
            
            ws.onopen = () => {
                status.textContent = 'Connected';
                status.className = 'status connected';
            };
            
            ws.onclose = () => {
                status.textContent = 'Disconnected';
                status.className = 'status disconnected';
                setTimeout(connect, 3000);
            };
            
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'message') {
                    addMessage(data.content, data.sender);
                } else if (data.type === 'chunk') {
                    appendToLastMessage(data.content);
                }
            };
        }
        
        function addMessage(content, sender) {
            const div = document.createElement('div');
            div.className = 'message ' + sender;
            div.textContent = content;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }
        
        function appendToLastMessage(content) {
            const messages = chat.querySelectorAll('.message.assistant');
            if (messages.length > 0) {
                const last = messages[messages.length - 1];
                last.textContent += content;
                chat.scrollTop = chat.scrollHeight;
            }
        }
        
        function sendMessage() {
            const text = messageInput.value.trim();
            if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
            
            addMessage(text, 'user');
            ws.send(JSON.stringify({type: 'message', content: text}));
            messageInput.value = '';
        }
        
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
        
        connect();
    </script>
</body>
</html>
"""


def create_app(settings: Settings) -> FastAPI:
        app = FastAPI(title="GrizzyClaw", version=__version__)
    agent = AgentCore(settings)
    security = SecurityManager(settings.secret_key)
    rate_limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window)

    @app.get("/")
    async def root():
        return HTMLResponse(content=HTML_TEMPLATE)

    @app.get("/health")
    async def health():
        llm_health = await agent.llm_router.health_check()
        return {"status": "healthy", "llm_providers": llm_health}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        client_id = security.generate_session_id()
        logger.info(f"Client connected: {client_id}")

        try:
            while True:
                data = await websocket.receive_text()
                message_data = json.loads(data)

                if message_data.get("type") == "message":
                    user_message = message_data.get("content", "")

                    # Send acknowledgment
                    await websocket.send_json(
                        {"type": "message", "sender": "user", "content": user_message}
                    )

                    # Stream response
                    response_text = ""
                    async for chunk in agent.process_message(client_id, user_message):
                        response_text += chunk
                        await websocket.send_json({"type": "chunk", "content": chunk})

                    # Send complete message
                    await websocket.send_json(
                        {
                            "type": "message",
                            "sender": "assistant",
                            "content": response_text,
                            "complete": True,
                        }
                    )

        except WebSocketDisconnect:
            logger.info(f"Client disconnected: {client_id}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            await websocket.close()

    @app.get("/api/memory/{user_id}")
    async def get_memory(user_id: str):
        return await agent.get_user_memory(user_id)

    @app.post("/api/chat")
    async def chat_endpoint(request: Dict[str, Any]):
        user_id = request.get("user_id", "anonymous")
        message = request.get("message", "")

        if not message:
            raise HTTPException(status_code=400, detail="Message required")

        response_chunks = []
        async for chunk in agent.process_message(user_id, message):
            response_chunks.append(chunk)

        return {"response": "".join(response_chunks), "user_id": user_id}

    return app
