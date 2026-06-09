import { NextRequest, NextResponse } from "next/server";

// Proxy to the lead-generation AGENT server (separate process, default :8001),
// distinct from the platform backend proxy (/api/proxy → :8000). Lets the
// browser trigger hunts and poll job status without CORS or a hardcoded host.
const AGENT_URL = process.env.AGENT_URL ?? "http://127.0.0.1:8001";

export const dynamic = "force-dynamic";

async function forward(req: NextRequest, segments: string[]) {
  const upstream = `${AGENT_URL}/${segments.join("/")}${req.nextUrl.search}`;
  const init: RequestInit = {
    method: req.method,
    headers: {
      "content-type": req.headers.get("content-type") ?? "application/json",
    },
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }
  let res: Response;
  try {
    res = await fetch(upstream, init);
  } catch (e) {
    return NextResponse.json(
      {
        detail: `Agent server unreachable at ${AGENT_URL}: ${(e as Error).message}`,
      },
      { status: 502 },
    );
  }
  const headers = new Headers();
  const ct = res.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  return new NextResponse(res.body, { status: res.status, headers });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return forward(req, path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return forward(req, path);
}
