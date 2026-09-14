on accountForAddress(senderAddress)
	tell application "Mail"
		repeat with candidateAccount in accounts
			try
				if (email addresses of candidateAccount) contains senderAddress then return name of candidateAccount
			end try
		end repeat
	end tell
	error "No Apple Mail account is configured for sender " & senderAddress
end accountForAddress

on messageMatches(candidateMessage, recipientAddress, expectedAttachmentName, deliveryReference)
	tell application "Mail"
		if deliveryReference is "" then return false
		if (content of candidateMessage) does not contain ("Delivery reference: " & deliveryReference) then return false
		set recipientMatched to false
		repeat with candidateRecipient in (to recipients of candidateMessage)
			if (address of candidateRecipient) is recipientAddress then set recipientMatched to true
		end repeat
		if not recipientMatched then return false

		set attachmentMatched to false
		repeat with candidateAttachment in (mail attachments of candidateMessage)
			try
				if (name of candidateAttachment) is expectedAttachmentName then set attachmentMatched to true
			end try
		end repeat
		return attachmentMatched
	end tell
end messageMatches

on run argv
	if (count of argv) is not 8 then
		error "usage: mail-kindle.applescript <draft|send|count-sent> <from> <to> <subject> <body> <attachment> <filename> <delivery-reference>"
	end if

	set operation to item 1 of argv
	set senderAddress to item 2 of argv
	set recipientAddress to item 3 of argv
	set messageSubject to item 4 of argv
	set messageBody to item 5 of argv
	set attachmentPath to item 6 of argv
	set expectedAttachmentName to item 7 of argv
	set deliveryReference to item 8 of argv
	set senderAccountName to my accountForAddress(senderAddress)

	if operation is "count-sent" then
		tell application "Mail"
			set senderAccount to account senderAccountName
			set sentBox to mailbox "Sent Messages" of senderAccount
			set cutoffDate to (current date) - (30 * days)
			set candidates to every message of sentBox whose subject is messageSubject and date sent is greater than cutoffDate
			set matchingCount to 0
			repeat with candidateMessage in candidates
				if my messageMatches(candidateMessage, recipientAddress, expectedAttachmentName, deliveryReference) then set matchingCount to matchingCount + 1
			end repeat
			return matchingCount as text
		end tell
	end if

	if operation is not "draft" and operation is not "send" then error "unsupported operation: " & operation
	set attachmentFile to POSIX file attachmentPath
	tell application "Mail"
		activate
		set newMessage to make new outgoing message with properties {subject:messageSubject, visible:(operation is "draft")}
		tell newMessage
			set sender to senderAddress
			set message signature to missing value
			set content to messageBody & return
			make new to recipient at end of to recipients with properties {address:recipientAddress}
			tell content
				make new attachment with properties {file name:attachmentFile} at after the last paragraph
			end tell
		end tell
		if operation is "draft" then
			save newMessage
			return "draft created"
		end if
		send newMessage
		return "send requested"
	end tell
end run
