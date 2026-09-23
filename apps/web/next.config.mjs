/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The twin talks to the FastAPI service. Proxying in development keeps the browser on
  // one origin so there is no CORS story to get wrong.
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.LCET_API ?? 'http://127.0.0.1:8099'}/:path*` }];
  },
};
export default nextConfig;
