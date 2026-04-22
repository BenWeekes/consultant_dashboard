import type { Metadata } from "next"
import { Open_Sans, Raleway } from "next/font/google"
import { ThemeProvider } from "next-themes"
import "./globals.css"

const headingFont = Raleway({
  variable: "--font-geist-sans",
  subsets: ["latin"],
})

const bodyFont = Open_Sans({
  variable: "--font-geist-mono",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: "MindFix",
  description: "AI mental wellness, guided by a human therapist.",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${headingFont.variable} ${bodyFont.variable} antialiased`}>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
          {children}
        </ThemeProvider>
      </body>
    </html>
  )
}
