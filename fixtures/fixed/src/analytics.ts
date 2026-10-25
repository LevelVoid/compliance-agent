import axios from "axios";

const ANALYTICS_ENDPOINT = "https://analytics.vendor-example.com/collect";

// Pseudonymous only: no direct identifiers leave the boundary.
export async function trackSignup(user) {
  return axios.post(ANALYTICS_ENDPOINT, {
    event: "signup",
    user_id: user.id,
    plan: user.plan,
  });
}

export async function trackPageview(page) {
  return axios.post(ANALYTICS_ENDPOINT, { event: "pageview", page });
}
