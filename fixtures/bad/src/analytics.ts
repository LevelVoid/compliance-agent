import axios from "axios";

const ANALYTICS_ENDPOINT = "https://analytics.vendor-example.com/collect";

export async function trackSignup(user) {
  return axios.post(ANALYTICS_ENDPOINT, {
    event: "signup",
    email: user.email,
    dateOfBirth: user.dob,
    plan: user.plan,
  });
}

export async function trackPageview(page) {
  return axios.post(ANALYTICS_ENDPOINT, { event: "pageview", page });
}
