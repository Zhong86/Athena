# Athena
A personal study agent that understands the student's current topics and help them improve. A project created for the AI Hackfest Hackathon by IDwebhost.
## Features
### Materials
Knowledge sync with student's Google Drive files (recommended to be used with institution email). The files and notes are divided into Topics instead of just 1 file through Semantic Chunking. This can be manually run or automatically through CRON job. 
### Goal and Roadmap
Help students achieve a specific goal; can be something simple like "Passing this quiz" or something more grand like "Getting into this internship". Milestones are built based on existing materials. It is editable and customizable by the users. 
### Quizzes
Quizzes is made from existing materials to help users review. This is where the score-generation for each material will happen. 

## Deployment
Pushing to `main` builds both images in GitHub Actions and rolls them out to the VPS over SSH. See [DEPLOY.md](DEPLOY.md) for the one-time server setup and the required repository secrets.

## Future Improvements
- Turning this into a mobile app and make this runnable on a local device.
- Focus on allowing Athena to browse from external resources to help create Goals beyond materials.
- Integrate with more note-taking software like Notion
- Have adaptive memory so each user can have a custom Athena to help with each of their learning style. 
