import os
import json
import asyncio
from openai import AsyncOpenAI

# A simple script demonstrating multi-agent collaboration for IncidentEnv
# Requires OPENAI_API_KEY environment variable.

client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

async def get_db_expert_opinion(observation):
    prompt = f"""You are the DB Expert. Look at the observation and state ONLY DB related issues.
    Observation: {json.dumps(observation, indent=2)}
    Focus only on postgres-db metrics and DB alerts. Do you see a root cause?"""
    
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )
    return response.choices[0].message.content

async def get_app_expert_opinion(observation):
    prompt = f"""You are the App Expert. Look at the observation and state ONLY Application issues.
    Observation: {json.dumps(observation, indent=2)}
    Focus on cpu, memory, latency of api-gateway, auth-service, payment-service. Do you see a root cause?"""
    
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )
    return response.choices[0].message.content

async def run_multi_agent():
    import requests
    
    API_URL = "http://localhost:7860"
    
    # 1. Reset env to medium task (DB issue)
    print("🤖 Multi-Agent Team Starting Incident Investigation...")
    res = requests.post(f"{API_URL}/reset", json={"task_id": "task_medium"})
    if res.status_code != 200:
        print("Server not running. Run: uvicorn app.main:app --reload")
        return
        
    observation = res.json()
    
    # 2. Get experts opinions
    print("\n🔍 DB Expert analyzing...")
    db_opinion = await get_db_expert_opinion(observation)
    print(db_opinion)
    
    print("\n🔍 App Expert analyzing...")
    app_opinion = await get_app_expert_opinion(observation)
    print(app_opinion)
    
    # 3. Manager decides action based on experts (simplified)
    print("\n👔 Manager Agent consolidating and taking action...")
    manager_prompt = f"""Based on DB expert: "{db_opinion}" and App expert: "{app_opinion}",
    what fix tool should we apply? Choose exactly one tool string from the available fixes list here: 
    {observation['available_fixes']}
    Reply with ONLY the string name of the fix."""
    
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": manager_prompt}],
        temperature=0.0
    )
    chosen_fix = response.choices[0].message.content.strip()
    print(f"Manager decided to apply fix: {chosen_fix}")
    
    # Apply fix
    res = requests.post(f"{API_URL}/step", json={"action_type": "apply_fix", "tool": chosen_fix})
    step_res = res.json()
    print(f"\nResult: {step_res['info']['feedback']}")

if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("Set OPENAI_API_KEY to run multi-agent example.")
    else:
        asyncio.run(run_multi_agent())
