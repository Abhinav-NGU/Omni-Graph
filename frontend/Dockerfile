FROM node:20-alpine

WORKDIR /app
 
# Copy package.json and package-lock.json (if available) to leverage Docker cache.
# This step will only be re-run if these files change.
COPY package*.json ./
 
# Install dependencies
RUN npm install
 
# Copy the rest of the application source code
COPY . .
 
# Build the Next.js application
RUN npm run build
 
# The command to run when the container starts
CMD ["npm", "start"]